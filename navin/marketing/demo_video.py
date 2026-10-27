# Copyright (c) 2026-present Navinspire IA
# SPDX-License-Identifier: AGPL-3.0-only

"""Premium demo-video pipeline for the Marketing desk.

brief -> storyboard JSON -> capture shot list -> footage_polish ->
motion_render (per scene, cached) -> premium assemble -> video QA ->
targeted revise (up to two rounds) -> deliverables in 16:9, 9:16 and 1:1
with a thumbnail, posters and social cover stills.

Every heavy step sits behind one callable in :class:`DemoVideoBackends`, so
the orchestration is testable with fakes and each Montage layer (browser
capture, footage polish, motion renderer, premium assemble, narration,
vision) can evolve on its own. Scenes render one at a time and are cached by
content hash: a revise round only re-renders the scenes whose storyboard
entry changed.
"""

from __future__ import annotations

import asyncio
import copy
import hashlib
import inspect
import json
import re
import shutil
from collections.abc import Callable, Sequence
from dataclasses import dataclass, field
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from navin.marketing.video_qa import VideoQAPolicy, failing_scenes, run_video_qa
from navin.utils.atomic_io import atomic_write_text

MOTION_TEMPLATES: tuple[str, ...] = (
    "hero_intro",
    "kinetic_title",
    "feature_callout",
    "device_mockup_3d",
    "stats_counter",
    "split_compare",
    "lower_third",
    "cta_end_card",
    "logo_sting",
    "gradient_stage",
)
FOOTAGE = "footage"
THREE_D_TEMPLATES = frozenset({"device_mockup_3d", "hero_intro"})
HOOK_TEMPLATES = frozenset({"hero_intro", "kinetic_title", "device_mockup_3d"})
FORMATS: dict[str, tuple[int, int]] = {"16:9": (1920, 1080), "9:16": (1080, 1920), "1:1": (1080, 1080)}
DEFAULT_FORMATS: tuple[str, ...] = ("16:9", "9:16", "1:1")
MAX_REVISIONS = 2
MIN_TOTAL_S = 15
MAX_TOTAL_S = 45
_SECRET_KEY = re.compile(r"(pass(word|wd)?|token|secret|api[_-]?key|cookie|otp|credential|bearer)", re.I)
_EMAIL = re.compile(r"[\w.+-]+@[\w-]+\.[\w.-]+")
_DASHES = {"—": "-", "–": "-", "‒": "-", "―": "-"}
#: Login block of a footage scene: saved-secret references only, never values.
_LOGIN_KEYS = ("secret_ref", "username_ref", "totp_ref", "url")
_MEDIA_SUFFIXES = frozenset({".png", ".jpg", ".jpeg", ".webp", ".gif", ".mp4", ".mov", ".webm", ".mkv"})

# Props of the built-in motion templates (navin.montage.motion.catalog). The
# live catalog overrides these when the renderer is installed.
_TEXT_PROPS: dict[str, tuple[str, ...]] = {
    "hero_intro": ("headline", "subheadline", "eyebrow"),
    "kinetic_title": ("lines", "eyebrow"),
    "feature_callout": ("label", "title"),
    "device_mockup_3d": ("headline",),
    "stats_counter": ("title",),
    "split_compare": ("title", "before_label", "after_label"),
    "lower_third": ("name", "role"),
    "cta_end_card": ("headline", "button"),
    "logo_sting": ("tagline",),
    "gradient_stage": ("title", "subtitle", "eyebrow"),
}
_MEDIA_PROPS: dict[str, tuple[str, ...]] = {
    "feature_callout": ("media",),
    "device_mockup_3d": ("media",),
    "split_compare": ("before", "after"),
}
_MIN_DURATION: dict[str, float] = {
    "hero_intro": 2.5,
    "feature_callout": 2.5,
    "device_mockup_3d": 2.5,
    "kinetic_title": 1.5,
    "logo_sting": 1.5,
    "gradient_stage": 1.0,
}

Backend = Callable[..., Any]


class DemoVideoError(RuntimeError):
    """The pipeline cannot deliver (missing renderer, every scene failed...)."""


@dataclass
class DemoVideoBackends:
    """Pluggable steps. Any callable may be sync or async.

    * ``templates()`` -> template names or catalog dicts the renderer knows.
    * ``author(system, user)`` -> storyboard dict / JSON text, or None.
    * ``capture(shot, out_dir)`` -> ``{"video": path, "actions": path}`` or None.
    * ``polish(video=, actions=, format=, output=, options=)`` -> polished path.
    * ``render(storyboard, output)`` -> MP4 for a one-scene storyboard (the
      storyboard carries ``width``/``height`` of the canvas).
    * ``narrate(segments, out_dir)`` -> ``{"voice_cues": [{path, at}],
      "caption_segments": [...]}`` (or legacy ``{"voice": path}``).
    * ``assemble(job)`` -> final MP4 path (``job["quality"] == "premium"``).
    * ``vision`` -> a visual QA provider exposing ``analyze_video``.
    """

    render: Backend | None = None
    assemble: Backend | None = None
    templates: Backend | None = None
    author: Backend | None = None
    capture: Backend | None = None
    polish: Backend | None = None
    narrate: Backend | None = None
    beats: Backend | None = None
    vision: Any | None = None
    qa_policy: VideoQAPolicy = field(default_factory=VideoQAPolicy)


async def _call(fn: Backend, *args: Any, **kwargs: Any) -> Any:
    if inspect.iscoroutinefunction(fn):
        return await fn(*args, **kwargs)
    result = await asyncio.to_thread(fn, *args, **kwargs)
    if inspect.isawaitable(result):
        return await result
    return result


def _clean(text: Any) -> str:
    body = str(text or "")
    for bad, good in _DASHES.items():
        body = body.replace(bad, good)
    return body.strip()


def _scrub(value: Any) -> Any:
    """Drop credential-looking keys and literal emails from model output."""
    if isinstance(value, dict):
        out: dict[str, Any] = {}
        for key, item in value.items():
            name = str(key)
            if not name.endswith("_ref") and _SECRET_KEY.search(name) and not name.endswith("_selector"):
                continue
            out[name] = _scrub(item)
        return out
    if isinstance(value, list):
        return [_scrub(item) for item in value]
    if isinstance(value, str):
        return _EMAIL.sub("user@example.com", _clean(value))
    return value


def _words(text: Any, limit: int = 6) -> str:
    return " ".join(str(text or "").split()[:limit])


# ---------------------------------------------------------------------------
# Template catalog and props
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class TemplateCatalog:
    """What the storyboard may use: names, min durations, required props."""

    names: tuple[str, ...]
    info: dict[str, dict[str, Any]] = field(default_factory=dict)

    @classmethod
    def from_listing(cls, listing: Sequence[Any] | None) -> TemplateCatalog:
        names: list[str] = []
        info: dict[str, dict[str, Any]] = {}
        for item in listing or []:
            if isinstance(item, dict) and item.get("name"):
                names.append(str(item["name"]))
                info[str(item["name"])] = item
            elif isinstance(item, str) and item.strip():
                names.append(item.strip())
        return cls(tuple(names) or MOTION_TEMPLATES, info)

    def min_duration(self, template: str) -> float:
        raw = (self.info.get(template) or {}).get("min_duration_s")
        if isinstance(raw, int | float) and not isinstance(raw, bool):
            return float(raw)
        return _MIN_DURATION.get(template, 2.0)

    def required(self, template: str) -> tuple[str, ...]:
        schema = (self.info.get(template) or {}).get("props_schema") or {}
        listed = schema.get("required") if isinstance(schema, dict) else None
        if isinstance(listed, list):
            return tuple(str(item) for item in listed)
        return _MEDIA_PROPS.get(template, ()) + (("lines",) if template == "kinetic_title" else ()) + (
            ("stats",) if template == "stats_counter" else ()
        )

    def prompt_listing(self) -> str:
        lines: list[str] = []
        for name in self.names:
            row = self.info.get(name) or {}
            schema = row.get("props_schema") or {}
            props = list((schema.get("properties") or {}).keys()) if isinstance(schema, dict) else []
            props = props or list(_TEXT_PROPS.get(name, ())) + list(_MEDIA_PROPS.get(name, ()))
            required = ", ".join(self.required(name)) or "none"
            description = str(row.get("description") or "").strip()
            lines.append(
                f"- `{name}`: {description} props: {', '.join(props) or 'none'}; required: {required}; "
                f"min {self.min_duration(name)} s."
            )
        return "\n".join(lines)


def _duration_bounds(template: str, catalog: TemplateCatalog) -> tuple[float, float]:
    if template == FOOTAGE:
        return 3.0, 6.0
    if template == "logo_sting":
        return max(1.5, catalog.min_duration(template)), 3.0
    low = max(2.0, catalog.min_duration(template))
    return low, max(low, 4.0)


def _looks_like_media(value: Any) -> bool:
    text = str(value or "").strip()
    return bool(text) and "\n" not in text and not text.startswith(("http://", "https://", "data:")) and (
        Path(text).suffix.lower() in _MEDIA_SUFFIXES
    )


def _text_of(props: dict[str, Any]) -> str:
    for key in ("headline", "title", "label", "name", "tagline", "subtitle", "subheadline", "caption", "button"):
        if str(props.get(key) or "").strip():
            return str(props[key]).strip()
    lines = props.get("lines")
    if isinstance(lines, list) and lines:
        return " ".join(str(item) for item in lines)
    return ""


def adapt_scene(
    scene: dict[str, Any],
    *,
    catalog: TemplateCatalog,
    media: list[str],
    title: str,
) -> dict[str, Any]:
    """Map generic storyboard props onto the template contract.

    Models (and older storyboards) write ``title``/``subtitle``/``cta``/
    ``src``; templates want ``headline``/``subheadline``/``button``/``media``.
    A template whose required media is missing becomes a scene that needs
    none, so a render never fails on a blank layer, and media paths that do
    not exist are dropped instead of failing the whole render.
    """
    out = copy.deepcopy(scene)
    template = str(out.get("template") or "")
    props = dict(out.get("props") or {})
    if template == FOOTAGE:
        out["props"] = props
        return out
    for key, value in list(props.items()):
        if isinstance(value, str) and _looks_like_media(value) and not Path(value).expanduser().is_file():
            props.pop(key)
    text = _clean(props.get("title") or props.get("headline") or props.get("caption") or "")
    if template in {"hero_intro", "device_mockup_3d", "cta_end_card"}:
        if not props.get("headline") and text:
            props["headline"] = text
        props.pop("title", None)
    if template == "hero_intro" and not props.get("subheadline") and props.get("subtitle"):
        props["subheadline"] = props.pop("subtitle")
    if template == "cta_end_card":
        if not props.get("button") and props.get("cta"):
            props["button"] = _words(props.pop("cta"), 4)
        props.pop("title", None)
    if template == "kinetic_title":
        lines = props.get("lines")
        if isinstance(lines, str):
            lines = [lines]
        if not isinstance(lines, list) or not any(str(item).strip() for item in lines):
            lines = [text or title]
        props["lines"] = [_words(item) for item in lines if str(item).strip()][:4] or [_words(title)]
        props.pop("title", None)
    if template == "logo_sting" and not props.get("tagline") and props.get("title"):
        props["tagline"] = _words(props.pop("title"), 5)
    if template in _MEDIA_PROPS:
        for key in ("src", "screen", "image", "screenshot"):
            if not props.get("media") and props.get(key) and template != "split_compare":
                props["media"] = props.pop(key)
        pool = [item for item in media if Path(item).is_file()]
        for key in _MEDIA_PROPS[template]:
            if not props.get(key) and pool:
                # split_compare "after" takes a second screenshot when there is one.
                props[key] = pool[1] if key == "after" and len(pool) > 1 else pool[0]
    if template == "stats_counter":
        stats = [
            item for item in props.get("stats") or []
            if isinstance(item, dict) and isinstance(item.get("value"), int | float) and str(item.get("label") or "").strip()
        ]
        props["stats"] = stats[:4]
    missing = [key for key in catalog.required(template) if not props.get(key)]
    if missing:
        fallback_text = _text_of(props) or title
        if template == "device_mockup_3d" and "hero_intro" in catalog.names:
            out["template"] = "hero_intro"
            props = {"headline": _words(fallback_text, 8)}
        else:
            out["template"] = "gradient_stage" if "gradient_stage" in catalog.names else "kinetic_title"
            props = (
                {"title": _words(fallback_text, 8)}
                if out["template"] == "gradient_stage"
                else {"lines": [_words(fallback_text)]}
            )
    out["props"] = props
    return out


# ---------------------------------------------------------------------------
# Brief and storyboard
# ---------------------------------------------------------------------------


def demo_video_brief(store: Any) -> str:
    """The launch kit's "Demo video brief", or a fact-bound fallback."""
    launch = store.load_launch() if hasattr(store, "load_launch") else {}
    for item in (launch or {}).get("items") or []:
        if isinstance(item, dict) and item.get("id") == "demo_video" and str(item.get("body") or "").strip():
            return _clean(item["body"])
    from navin.marketing.content import product_facts

    facts = product_facts(store)
    return _clean(
        f"Demo video brief for {facts['name']}: {facts['one_liner'] or facts['pain'] or 'show the core workflow'}. "
        f"Audience: {facts['audience'] or 'buyers of this product'}. "
        f"Show the real product UI on {facts['site'] or 'the live app'}, one feature per scene, "
        f"end with CTA: {facts['cta'] or 'start free'}."
    )


def _prompt_template(name: str, workspace: Path | None) -> str:
    if workspace is not None:
        from navin.utils.workspace_prompts import (
            load_workspace_prompt_override,
            workspace_prompt_file,
        )

        text, _chars = load_workspace_prompt_override(workspace_prompt_file(workspace, name))
        if text:
            return text
    import navin.templates as templates_pkg

    return (Path(templates_pkg.__file__).parent / "prompts" / f"{name}.md").read_text(encoding="utf-8")


def _fill(template: str, **values: Any) -> str:
    out = template
    for key, value in values.items():
        out = out.replace(f"<<{key}>>", str(value))
    return out


def storyboard_prompt(
    *,
    brief: str,
    facts: dict[str, Any],
    brand: dict[str, Any],
    catalog: TemplateCatalog,
    media: Sequence[str] = (),
    fmt: str = "16:9",
    fps: int = 30,
    workspace: Path | None = None,
) -> str:
    listing = catalog.prompt_listing()
    if media:
        listing += "\n\nReal product screenshots / clips you may use as `media`:\n" + "\n".join(
            f"- {item}" for item in media[:8]
        )
    return _fill(
        _prompt_template("demo_storyboard", workspace),
        templates=listing,
        format=fmt,
        fps=fps,
        min_s=MIN_TOTAL_S,
        max_s=MAX_TOTAL_S,
        brand=json.dumps(brand, ensure_ascii=False, default=str),
        facts="\n".join(f"- {key}: {value}" for key, value in facts.items() if value),
        brief=brief,
    )


def _parse_json(raw: Any) -> dict[str, Any] | None:
    if isinstance(raw, dict):
        return raw
    if not isinstance(raw, str) or not raw.strip():
        return None
    from navin.providers.visual_qa import VisualQAProviderError, _extract_json

    try:
        return _extract_json(raw)
    except VisualQAProviderError:
        return None


def brand_block(facts: dict[str, Any], brand: dict[str, Any], harvest: dict[str, Any] | None = None) -> dict[str, Any]:
    """Brand kit dict the motion renderer and the caption styler both accept."""
    harvest = harvest or {}
    colors = [str(item) for item in (brand.get("colors") or harvest.get("colors") or []) if str(item).strip()]
    fonts = brand.get("fonts") or harvest.get("fonts") or []
    font = str(brand.get("font") or (fonts[0] if isinstance(fonts, list) and fonts else "") or "")
    return {
        "name": str(facts.get("name") or brand.get("product") or ""),
        "colors": colors[:5] or ["#0f172a", "#38bdf8"],
        "fonts": {"heading": font, "body": font},
        "logo": str(brand.get("logo") or ""),
        "tagline": str(facts.get("one_liner") or "")[:120],
        "url": str(facts.get("site") or ""),
    }


def normalize_storyboard(
    raw: dict[str, Any] | None,
    *,
    templates: Sequence[str] | TemplateCatalog,
    brand: dict[str, Any],
    fmt: str = "16:9",
    fps: int = 30,
    login: dict[str, Any] | None = None,
    fallback_title: str = "",
    media: Sequence[str] = (),
) -> dict[str, Any]:
    """Make an untrusted storyboard renderable and on-brief.

    Enforces the motion-design rules the template prompt asks for: known
    templates only, 2-4 s scenes (footage 3-6 s), a hook scene first, at
    least one three.js scene, a final CTA, no secrets, no long dashes, and
    props that match each template's contract.
    """
    catalog = templates if isinstance(templates, TemplateCatalog) else TemplateCatalog.from_listing(list(templates))
    allowed = set(catalog.names) | {FOOTAGE}
    pool = [str(item) for item in media]
    title = str(brand.get("name") or fallback_title or "Product demo")
    data = _scrub(copy.deepcopy(raw or {}))
    scenes: list[dict[str, Any]] = []
    for item in data.get("scenes") or []:
        if not isinstance(item, dict):
            continue
        template = str(item.get("template") or "").strip()
        if template not in allowed:
            continue
        props = item.get("props") if isinstance(item.get("props"), dict) else {}
        if template == FOOTAGE:
            given = props.get("login") if isinstance(props.get("login"), dict) else {}
            merged = {**(login or {}), **{k: v for k, v in given.items() if v}}
            props["login"] = {key: str(merged[key]) for key in _LOGIN_KEYS if merged.get(key)}
            if not props["login"]:
                props.pop("login")
            props["steps"] = [step for step in props.get("steps") or [] if isinstance(step, dict)]
        scene = adapt_scene(
            {"template": template, "duration": item.get("duration"), "props": props,
             "transition": str(item.get("transition") or "fade"), "narration": _clean(item.get("narration"))},
            catalog=catalog, media=pool, title=title,
        )
        scenes.append(scene)
    if not scenes or scenes[0]["template"] not in HOOK_TEMPLATES:
        scenes.insert(0, {"template": "hero_intro", "duration": 2.5,
                          "props": {"headline": _words(title, 8)}, "transition": "fade", "narration": ""})
    if not any(scene["template"] in THREE_D_TEMPLATES for scene in scenes):
        screen = next((item for item in pool if Path(item).is_file()), "")
        scenes.insert(1, adapt_scene(
            {"template": "device_mockup_3d", "duration": 3.0,
             "props": {"headline": _words(title, 8), **({"media": screen} if screen else {})},
             "transition": "fade", "narration": ""},
            catalog=catalog, media=pool, title=title,
        ))
    tail = [scene["template"] for scene in scenes[-2:]]
    if "cta_end_card" not in tail and "cta_end_card" in allowed:
        scenes.append({"template": "cta_end_card", "duration": 3.0,
                       "props": {"headline": _words(title, 8), "button": "Start free"},
                       "transition": "fade", "narration": ""})
    for scene in scenes:
        low, high = _duration_bounds(scene["template"], catalog)
        try:
            duration = float(scene.get("duration") or low)
        except (TypeError, ValueError):
            duration = low
        scene["duration"] = round(min(high, max(low, duration)), 2)
    scenes = scenes[:12]
    # Trim middle scenes until the cut fits; the hook and the CTA stay.
    while sum(scene["duration"] for scene in scenes) > MAX_TOTAL_S and len(scenes) > 3:
        scenes.pop(len(scenes) // 2)
    audio = data.get("audio") if isinstance(data.get("audio"), dict) else {}
    given_brand = data.get("brand") if isinstance(data.get("brand"), dict) else {}
    out_fmt = str(data.get("format") or fmt)
    return {
        "version": 1,
        "format": out_fmt if out_fmt in FORMATS else fmt,
        "fps": int(data.get("fps")) if data.get("fps") in (30, 60) else (fps if fps in (30, 60) else 30),
        "title": _clean(data.get("title")) or f"{title} demo",
        "brand": {**brand, **{k: v for k, v in given_brand.items() if v and k in {"colors", "fonts", "tagline"}}},
        "scenes": scenes,
        "audio": {
            "music": audio.get("music", "auto"),
            "voiceover": audio.get("voiceover", True) is not False,
            "captions": audio.get("captions", True) is not False,
            "snap_to_beats": audio.get("snap_to_beats", True) is not False,
        },
    }


def fallback_storyboard(
    facts: dict[str, Any], brand: dict[str, Any], *, site: str = "", media: Sequence[str] = ()
) -> dict[str, Any]:
    """Deterministic storyboard when no model is routed."""
    name = brand.get("name") or facts.get("name") or "Product"
    screen = next((item for item in media if Path(item).is_file()), "")
    scenes: list[dict[str, Any]] = [
        {"template": "hero_intro", "duration": 3,
         "props": {"headline": _words(name, 6), "subheadline": _words(facts.get("one_liner"), 8)},
         "transition": "fade", "narration": facts.get("one_liner") or ""},
    ]
    if facts.get("pain"):
        scenes.append({"template": "kinetic_title", "duration": 2.5, "props": {"lines": [_words(facts["pain"])]},
                       "transition": "slideleft", "narration": ""})
    if site:
        scenes.append({"template": FOOTAGE, "duration": 5,
                       "props": {"url": site, "steps": [{"action": "goto", "url": site}, {"action": "wait", "ms": 1200},
                                                         {"action": "scroll", "ms": 1500}]},
                       "transition": "fade", "narration": facts.get("value") or ""})
    scenes.append({"template": "device_mockup_3d", "duration": 3.5,
                   "props": {"headline": _words(facts.get("value") or name, 6), **({"media": screen} if screen else {})},
                   "transition": "fade", "narration": ""})
    scenes.append({"template": "cta_end_card", "duration": 3,
                   "props": {"headline": _words(name, 6), "button": _words(facts.get("cta") or "Start free", 4), "url": site},
                   "transition": "fade", "narration": facts.get("cta") or ""})
    scenes.append({"template": "logo_sting", "duration": 2, "props": {}, "transition": "fade", "narration": ""})
    return {"version": 1, "format": "16:9", "fps": 30, "title": f"{name} demo", "brand": brand,
            "scenes": scenes, "audio": {"music": "auto", "voiceover": True, "captions": True, "snap_to_beats": True}}


def capture_shot_list(storyboard: dict[str, Any], *, viewport: tuple[int, int] = (1920, 1080)) -> list[dict[str, Any]]:
    """One scripted browser capture per footage scene, login by secret_ref only."""
    shots: list[dict[str, Any]] = []
    for index, scene in enumerate(storyboard.get("scenes") or []):
        if scene.get("template") != FOOTAGE:
            continue
        props = scene.get("props") or {}
        shots.append(
            {
                "id": f"scene-{index:02d}",
                "scene_index": index,
                "url": str(props.get("url") or ""),
                "steps": list(props.get("steps") or []),
                "login": dict(props.get("login") or {}),
                "mask": list(props.get("mask") or []),
                "duration_hint": float(scene.get("duration") or 4),
                "viewport": {"width": viewport[0], "height": viewport[1], "device_scale_factor": 2},
            }
        )
    return shots


# ---------------------------------------------------------------------------
# Revise
# ---------------------------------------------------------------------------


def _auto_fix_scene(
    scene: dict[str, Any],
    issues: list[dict[str, Any]],
    round_no: int,
    *,
    catalog: TemplateCatalog,
    media: list[str],
    title: str,
) -> dict[str, Any]:
    fixed = copy.deepcopy(scene)
    props = fixed.setdefault("props", {})
    template = fixed.get("template")
    checks = {str(item.get("check")) for item in issues}
    if "text_legibility" in checks:
        for key in ("headline", "title", "label", "button", "tagline", "name"):
            if props.get(key):
                props[key] = _words(props[key], 5)
        for key in ("subheadline", "subtitle", "eyebrow", "role"):
            props.pop(key, None)
        if isinstance(props.get("lines"), list):
            props["lines"] = [_words(item, 4) for item in props["lines"]][:2]
    if checks & {"black_frames", "loading_or_blank"}:
        if template == FOOTAGE:
            props["trim_start"] = round(float(props.get("trim_start") or 0) + 0.75, 2)
            steps = list(props.get("steps") or [])
            if not steps or steps[0].get("action") != "wait":
                steps.insert(0, {"action": "wait", "ms": 1500})
            props["steps"] = steps
        elif template in _MEDIA_PROPS:
            props["video_start"] = round(float(props.get("video_start") or 0) + 0.75, 2)
        else:
            text = _text_of(props) or title
            fixed["template"] = "gradient_stage" if "gradient_stage" in catalog.names else template
            if fixed["template"] == "gradient_stage":
                fixed["props"] = props = {"title": _words(text, 8)}
    if "frozen_frames" in checks:
        if template == "device_mockup_3d":
            props["camera"] = "push"
        elif template == "feature_callout":
            props["zoom"] = 2.2
        elif template == FOOTAGE:
            props["motion"] = "push_in"
        fixed["duration"] = round(max(_duration_bounds(str(fixed["template"]), catalog)[0],
                                      float(fixed.get("duration") or 3) - 0.75), 2)
    if "secrets_visible" in checks and template in {FOOTAGE, *_MEDIA_PROPS}:
        # The take showed a credential: swap it for a safe screenshot in 3D,
        # or for a 3D hero with no screen at all.
        safe = next((item for item in media if Path(item).is_file() and item != props.get("media")
                     and item != props.get("src")), "")
        headline = _words(_text_of(props) or title, 6)
        fixed["template"] = "device_mockup_3d" if safe else "hero_intro"
        fixed["props"] = props = {"headline": headline, **({"media": safe} if safe else {})}
        fixed.pop("sources", None)
        fixed["duration"] = 3.0
    if "black_bars" in checks:
        props["fit"] = "cover"
    if "pacing" in checks:
        fixed["duration"] = max(_duration_bounds(str(fixed["template"]), catalog)[0], 3.0)
    props["revision"] = round_no
    return adapt_scene(fixed, catalog=catalog, media=media, title=title)


async def revise_storyboard(
    storyboard: dict[str, Any],
    report: dict[str, Any],
    *,
    author: Backend | None,
    templates: Sequence[str] | TemplateCatalog,
    round_no: int,
    workspace: Path | None = None,
    media: Sequence[str] = (),
) -> tuple[dict[str, Any], list[int]]:
    """Return the revised storyboard and the scene indexes that changed."""
    failing = failing_scenes(report)
    if not failing:
        return storyboard, []
    catalog = templates if isinstance(templates, TemplateCatalog) else TemplateCatalog.from_listing(list(templates))
    title = str((storyboard.get("brand") or {}).get("name") or storyboard.get("title") or "")
    pool = [str(item) for item in media]
    revised = copy.deepcopy(storyboard)
    model_scenes: list[dict[str, Any]] | None = None
    if author is not None:
        prompt = _fill(
            _prompt_template("demo_storyboard_revise", workspace),
            storyboard=json.dumps(
                {**storyboard, "scenes": [{k: v for k, v in s.items() if k != "sources"} for s in storyboard["scenes"]]},
                ensure_ascii=False, indent=1,
            ),
            findings=json.dumps(
                [
                    {k: item.get(k) for k in ("scene_index", "timestamp", "check", "status", "evidence", "recommendation")}
                    for rows in failing.values()
                    for item in rows
                ],
                ensure_ascii=False,
                indent=1,
            ),
        )
        try:
            answer = _parse_json(await _call(author, prompt, "Revise the storyboard."))
        except Exception:  # noqa: BLE001 - deterministic fixes still apply
            answer = None
        if answer and isinstance(answer.get("scenes"), list) and len(answer["scenes"]) == len(storyboard["scenes"]):
            model_scenes = answer["scenes"]
    allowed = set(catalog.names) | {FOOTAGE}
    changed: list[int] = []
    for index, issues in sorted(failing.items()):
        if not 0 <= index < len(revised["scenes"]):
            continue
        current = revised["scenes"][index]
        candidate = None
        if model_scenes is not None:
            proposal = _scrub(model_scenes[index])
            if isinstance(proposal, dict) and proposal.get("template") in allowed:
                low, high = _duration_bounds(str(proposal["template"]), catalog)
                proposal["duration"] = round(min(high, max(low, float(proposal.get("duration") or low))), 2)
                proposal.setdefault("props", {})["revision"] = round_no
                merged = {**current, **proposal}
                if proposal.get("template") != current.get("template"):
                    merged.pop("sources", None)
                candidate = adapt_scene(merged, catalog=catalog, media=pool, title=title)
                if candidate == current:
                    candidate = None
        revised["scenes"][index] = candidate or _auto_fix_scene(
            current, issues, round_no, catalog=catalog, media=pool, title=title
        )
        changed.append(index)
    return revised, changed


# ---------------------------------------------------------------------------
# Render helpers
# ---------------------------------------------------------------------------


def scene_key(scene: dict[str, Any], *, fmt: str, fps: int, brand: dict[str, Any], size: tuple[int, int]) -> str:
    payload = json.dumps({"scene": scene, "format": fmt, "fps": fps, "brand": brand, "size": list(size)},
                         sort_keys=True, default=str)
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()[:16]


def _scene_for_format(scene: dict[str, Any], fmt: str) -> dict[str, Any]:
    out = copy.deepcopy(scene)
    sources = out.pop("sources", None) or {}
    if out.get("template") == FOOTAGE:
        src = sources.get(fmt) or sources.get("master")
        if src:
            out.setdefault("props", {})["src"] = src
            # A polished take is already framed for this format.
            out["props"]["polished"] = bool(sources.get(fmt))
    return out


def _srt_time(seconds: float) -> str:
    millis = int(round(max(0.0, seconds) * 1000))
    hours, rest = divmod(millis, 3_600_000)
    minutes, rest = divmod(rest, 60_000)
    secs, millis = divmod(rest, 1000)
    return f"{hours:02d}:{minutes:02d}:{secs:02d},{millis:03d}"


TRANSITION_S = 0.4


def timeline(storyboard: dict[str, Any]) -> tuple[list[float], float]:
    """Scene start times and total length of the assembled cut.

    The assembler crossfades every cut when any scene asks for a transition,
    and each crossfade overlaps two scenes by :data:`TRANSITION_S`.
    """
    scenes = storyboard.get("scenes") or []
    overlap = TRANSITION_S if any(str(s.get("transition") or "none") != "none" for s in scenes) and len(scenes) > 1 else 0.0
    starts: list[float] = []
    cursor = 0.0
    for scene in scenes:
        starts.append(round(cursor, 3))
        cursor += float(scene.get("duration") or 0) - overlap
    return starts, round(cursor + overlap, 3)


def narration_segments(storyboard: dict[str, Any]) -> list[dict[str, Any]]:
    out: list[dict[str, Any]] = []
    starts, _total = timeline(storyboard)
    for index, scene in enumerate(storyboard.get("scenes") or []):
        duration = float(scene.get("duration") or 0)
        text = _clean(scene.get("narration"))
        if text:
            out.append({"scene_index": index, "start": starts[index], "end": round(starts[index] + duration, 3), "text": text})
    return out


def write_captions(segments: list[dict[str, Any]], target: Path) -> Path | None:
    """Plain SRT sidecar (platform closed captions); burned captions are ASS."""
    if not segments:
        return None
    lines: list[str] = []
    for number, segment in enumerate(segments, start=1):
        lines += [str(number), f"{_srt_time(segment['start'])} --> {_srt_time(segment['end'] - 0.05)}", segment["text"], ""]
    atomic_write_text(target, "\n".join(lines))
    return target


async def _frame(ffmpeg: str, video: Path, at: float, target: Path) -> Path | None:
    from navin.montage.ffmpeg_runner import run_process

    result = await run_process(
        [ffmpeg, "-hide_banner", "-nostdin", "-y", "-ss", f"{max(0.0, at):.3f}", "-i", str(video),
         "-frames:v", "1", "-q:v", "2", str(target)],
        timeout_s=120,
    )
    return target if result.ok and target.is_file() and target.stat().st_size > 0 else None


def _fit(source: Path, target: Path, size: tuple[int, int]) -> Path | None:
    try:
        from PIL import Image, ImageOps
    except ImportError:  # pragma: no cover
        return None
    with Image.open(source) as opened:
        image = ImageOps.fit(opened.convert("RGB"), size, method=Image.Resampling.LANCZOS)
    image.save(target, "JPEG", quality=90)
    return target


def _hero_time(storyboard: dict[str, Any]) -> float:
    starts, _total = timeline(storyboard)
    for index, scene in enumerate(storyboard["scenes"]):
        if scene["template"] in THREE_D_TEMPLATES:
            return starts[index] + float(scene["duration"]) * 0.6
    return float(storyboard["scenes"][0]["duration"]) * 0.6


def _cta_time(storyboard: dict[str, Any]) -> float:
    starts, total = timeline(storyboard)
    found = None
    for index, scene in enumerate(storyboard["scenes"]):
        if scene["template"] == "cta_end_card":
            found = starts[index] + float(scene["duration"]) * 0.6
    return found if found is not None else max(0.0, total - 1.0)


async def build_stills(storyboard: dict[str, Any], videos: dict[str, str], out_dir: Path) -> dict[str, Any]:
    """Thumbnail, one poster per format and social cover stills."""
    from navin.montage.detect import find_ffmpeg

    ffmpeg = await asyncio.to_thread(find_ffmpeg)
    if not ffmpeg:
        return {}
    out_dir.mkdir(parents=True, exist_ok=True)
    hero, cta = _hero_time(storyboard), _cta_time(storyboard)
    stills: dict[str, Any] = {"posters": {}, "covers": {}}
    for fmt, video in videos.items():
        slug = fmt.replace(":", "x")
        poster = await _frame(ffmpeg, Path(video), cta, out_dir / f"poster-{slug}.jpg")
        if poster:
            stills["posters"][fmt] = str(poster)
        raw = await _frame(ffmpeg, Path(video), hero, out_dir / f"hero-{slug}.jpg")
        if raw is None:
            continue
        if fmt == "16:9":
            thumb = await asyncio.to_thread(_fit, raw, out_dir / "thumbnail.jpg", (1280, 720))
            if thumb:
                stills["thumbnail"] = str(thumb)
            og = await asyncio.to_thread(_fit, raw, out_dir / "cover-landscape-1200x630.jpg", (1200, 630))
            if og:
                stills["covers"]["landscape"] = str(og)
        elif fmt == "9:16":
            cover = await asyncio.to_thread(_fit, raw, out_dir / "cover-vertical-1080x1920.jpg", (1080, 1920))
            if cover:
                stills["covers"]["vertical"] = str(cover)
        elif fmt == "1:1":
            cover = await asyncio.to_thread(_fit, raw, out_dir / "cover-square-1080x1080.jpg", (1080, 1080))
            if cover:
                stills["covers"]["square"] = str(cover)
    return stills


# ---------------------------------------------------------------------------
# Orchestrator
# ---------------------------------------------------------------------------


@dataclass
class _Run:
    out_dir: Path
    backends: DemoVideoBackends
    formats: tuple[str, ...]
    catalog: TemplateCatalog
    media: list[str] = field(default_factory=list)
    sizes: dict[str, tuple[int, int]] = field(default_factory=lambda: dict(FORMATS))
    log: list[dict[str, Any]] = field(default_factory=list)
    renders: int = 0

    def step(self, name: str, **data: Any) -> None:
        self.log.append({"step": name, "at": datetime.now(UTC).isoformat(), **data})


async def _capture_and_polish(
    run: _Run, storyboard: dict[str, Any], indexes: set[int] | None = None
) -> None:
    """Capture each footage scene once, then polish one take per format."""
    shots = capture_shot_list(storyboard)
    atomic_write_text(run.out_dir / "shots.json", json.dumps(shots, indent=2, ensure_ascii=False) + "\n")
    title = str((storyboard.get("brand") or {}).get("name") or "")
    for shot in shots:
        index = shot["scene_index"]
        if indexes is not None and index not in indexes:
            continue
        scene = storyboard["scenes"][index]
        captured = None
        if run.backends.capture is not None:
            try:
                captured = await _call(run.backends.capture, shot, run.out_dir / "capture")
            except Exception as exc:  # noqa: BLE001 - fall back to a 3D mockup
                run.step("capture_failed", scene_index=index, error=str(exc)[:200])
        video = str((captured or {}).get("video") or "")
        if not video or not Path(video).is_file():
            # No real take: a three.js device mockup with a real screenshot (or
            # a 3D hero) keeps the scene honest: no invented UI.
            props = scene.get("props") or {}
            storyboard["scenes"][index] = adapt_scene(
                {**scene, "template": "device_mockup_3d", "duration": min(4.0, float(scene.get("duration") or 3)),
                 "props": {"headline": _words(props.get("caption") or _text_of(props) or title, 6)}},
                catalog=run.catalog, media=run.media, title=title,
            )
            storyboard["scenes"][index].pop("sources", None)
            run.step("capture_fallback", scene_index=index, template=storyboard["scenes"][index]["template"])
            continue
        actions = (captured or {}).get("actions")
        sources: dict[str, str] = {"master": video}
        props = scene.get("props") or {}
        for fmt in run.formats:
            if run.backends.polish is None:
                break
            try:
                polished = await _call(
                    run.backends.polish,
                    video=video,
                    actions=actions,
                    format=fmt,
                    output=str(run.out_dir / "polish" / f"{shot['id']}-{fmt.replace(':', 'x')}.mp4"),
                    options={
                        "size": list(run.sizes[fmt]),
                        **{k: props[k] for k in ("trim_start", "fit", "mask", "motion", "caption") if k in props},
                    },
                )
            except Exception as exc:  # noqa: BLE001 - the raw take is still usable
                run.step("polish_failed", scene_index=index, format=fmt, error=str(exc)[:200])
                polished = None
            if polished and Path(str(polished)).is_file():
                sources[fmt] = str(polished)
        scene["sources"] = sources
        run.step("captured", scene_index=index, formats=sorted(sources))


def _safe_scene(scene: dict[str, Any], catalog: TemplateCatalog, title: str) -> dict[str, Any]:
    text = _text_of(scene.get("props") or {}) or title
    name = "gradient_stage" if "gradient_stage" in catalog.names else "kinetic_title"
    props = {"title": _words(text, 8)} if name == "gradient_stage" else {"lines": [_words(text)]}
    return {**{k: v for k, v in scene.items() if k != "sources"}, "template": name, "props": props}


async def _render_all(run: _Run, storyboard: dict[str, Any]) -> dict[str, list[str]]:
    """Render every scene of every format; a scene that fails once is swapped
    for a safe gradient scene in the storyboard so all formats stay in step."""
    title = str((storyboard.get("brand") or {}).get("name") or "")
    swapped: set[int] = set()
    while True:
        try:
            return {fmt: await _render_format(run, storyboard, fmt) for fmt in run.formats}
        except _SceneRenderError as exc:
            if exc.index in swapped:
                raise DemoVideoError(f"scene {exc.index} could not be rendered: {exc.reason}") from None
            swapped.add(exc.index)
            storyboard["scenes"][exc.index] = _safe_scene(storyboard["scenes"][exc.index], run.catalog, title)
            run.step("render_fallback", scene_index=exc.index, error=exc.reason[:200])


class _SceneRenderError(Exception):
    def __init__(self, index: int, reason: str) -> None:
        super().__init__(reason)
        self.index = index
        self.reason = reason


async def _render_format(run: _Run, storyboard: dict[str, Any], fmt: str) -> list[str]:
    folder = run.out_dir / "scenes" / fmt.replace(":", "x")
    folder.mkdir(parents=True, exist_ok=True)
    width, height = run.sizes[fmt]
    clips: list[str] = []
    for index, scene in enumerate(storyboard["scenes"]):
        one = _scene_for_format(scene, fmt)
        key = scene_key(one, fmt=fmt, fps=storyboard["fps"], brand=storyboard["brand"], size=(width, height))
        target = folder / f"{index:02d}-{key}.mp4"
        if not target.is_file():
            single = {"version": 1, "format": fmt, "fps": storyboard["fps"], "width": width, "height": height,
                      "brand": storyboard["brand"], "scenes": [one], "audio": {}}
            try:
                produced = await _call(run.backends.render, single, str(target))
            except Exception as exc:  # noqa: BLE001 - swapped for a safe scene
                raise _SceneRenderError(index, str(exc)) from exc
            path = Path(str(produced or target))
            if not path.is_file():
                raise _SceneRenderError(index, f"no clip for {one['template']}")
            if path != target:
                await asyncio.to_thread(shutil.copy2, path, target)
            run.renders += 1
            run.step("rendered", scene_index=index, format=fmt, template=one["template"])
        clips.append(str(target))
    return clips


async def _assemble_format(
    run: _Run, storyboard: dict[str, Any], fmt: str, clips: list[str], audio: dict[str, Any], options: dict[str, Any]
) -> str:
    width, height = run.sizes[fmt]
    output = run.out_dir / f"demo-{fmt.replace(':', 'x')}.mp4"
    job = {
        "clips": clips,
        "durations": [float(scene["duration"]) for scene in storyboard["scenes"]],
        "transitions": [str(scene.get("transition") or "none") for scene in storyboard["scenes"]],
        "transition_duration": TRANSITION_S,
        "format": fmt,
        "width": width,
        "height": height,
        "fps": storyboard["fps"],
        "brand": storyboard["brand"],
        "voice": audio.get("voice"),
        "voice_cues": audio.get("voice_cues") or [],
        "caption_segments": audio.get("caption_segments") or [],
        "captions": bool(storyboard["audio"].get("captions")),
        "captions_srt": audio.get("captions_srt"),
        "music": audio.get("music"),
        "snap_to_beats": bool(storyboard["audio"].get("snap_to_beats")),
        "quality": "premium",
        "loudness_target": run.backends.qa_policy.target_lufs,
        "work_dir": str(run.out_dir / "assemble"),
        "output": str(output),
        **options,
    }
    produced = await _call(run.backends.assemble, job)
    path = Path(str(produced or output))
    if not path.is_file():
        raise DemoVideoError(f"assemble produced no video for {fmt}")
    run.step("assembled", format=fmt, path=str(path))
    return str(path)


async def _snap_durations(run: _Run, board: dict[str, Any], music: str | None) -> None:
    """Move scene cuts onto the music beat grid (premium pacing)."""
    if not music or not board["audio"].get("snap_to_beats") or run.backends.beats is None:
        return
    try:
        beats = await _call(run.backends.beats, music)
    except Exception as exc:  # noqa: BLE001 - unsnapped cuts are still fine
        run.step("beats_failed", error=str(exc)[:200])
        return
    if not beats:
        return
    from navin.montage.audio import snap_to_beats

    ends: list[float] = []
    cursor = 0.0
    for scene in board["scenes"]:
        cursor += float(scene["duration"])
        ends.append(round(cursor, 3))
    snapped = snap_to_beats(ends[:-1], beats) + [ends[-1]]
    previous = 0.0
    for scene, end in zip(board["scenes"], snapped, strict=False):
        low, high = _duration_bounds(scene["template"], run.catalog)
        scene["duration"] = round(min(high + 0.5, max(low, end - previous)), 3)
        previous += scene["duration"]
    run.step("beats", cuts=len(snapped))


async def _narrate(run: _Run, board: dict[str, Any], music: str | None) -> dict[str, Any]:
    segments = narration_segments(board)
    audio: dict[str, Any] = {"segments": segments, "music": music, "caption_segments": segments}
    if segments:
        srt = write_captions(segments, run.out_dir / "captions.srt")
        audio["captions_srt"] = str(srt) if srt else None
    if board["audio"].get("voiceover") and segments and run.backends.narrate is not None:
        try:
            spoken = await _call(run.backends.narrate, segments, run.out_dir / "narration")
        except Exception as exc:  # noqa: BLE001 - captions still carry the message
            run.step("narration_failed", error=str(exc)[:200])
            spoken = None
        if isinstance(spoken, dict):
            audio["voice"] = spoken.get("voice")
            audio["voice_cues"] = list(spoken.get("voice_cues") or [])
            if spoken.get("caption_segments"):
                audio["caption_segments"] = list(spoken["caption_segments"])
            run.step("narrated", cues=len(audio["voice_cues"]) or int(bool(audio["voice"])))
    return audio


async def run_demo_video(
    *,
    brief: str,
    facts: dict[str, Any],
    brand: dict[str, Any],
    out_dir: str | Path,
    backends: DemoVideoBackends,
    formats: Sequence[str] = DEFAULT_FORMATS,
    fps: int = 30,
    login: dict[str, Any] | None = None,
    music: str | None = None,
    max_revisions: int = MAX_REVISIONS,
    workspace: Path | None = None,
    storyboard: dict[str, Any] | None = None,
    sizes: dict[str, tuple[int, int]] | None = None,
    media: Sequence[str] = (),
) -> dict[str, Any]:
    """Run the whole pipeline and return a manifest of the deliverables.

    ``media`` lists real product screenshots / clips (harvested from the
    site) the storyboard may place in 3D mockups. ``sizes`` overrides the
    canvas per format (draft renders, tests); the default is the full-HD
    deliverable canvas of :data:`FORMATS`.
    """
    if backends.render is None or backends.assemble is None:
        raise DemoVideoError("the demo video pipeline needs a motion renderer and an assembler")
    picked = tuple(fmt for fmt in formats if fmt in FORMATS) or DEFAULT_FORMATS
    root = Path(out_dir)
    root.mkdir(parents=True, exist_ok=True)
    listing: list[Any] = list(MOTION_TEMPLATES)
    if backends.templates is not None:
        try:
            listing = list(await _call(backends.templates) or []) or listing
        except Exception:  # noqa: BLE001 - the static list still renders
            listing = list(MOTION_TEMPLATES)
    catalog = TemplateCatalog.from_listing(listing)
    pool = [str(Path(item)) for item in media if Path(item).is_file()]
    run = _Run(out_dir=root, backends=backends, formats=picked, catalog=catalog, media=pool,
               sizes={**FORMATS, **(sizes or {})})
    primary = picked[0]

    # 1. Storyboard
    source = "given"
    raw = storyboard
    if raw is None and backends.author is not None:
        system = storyboard_prompt(brief=brief, facts=facts, brand=brand, catalog=catalog, media=pool,
                                   fmt=primary, fps=fps, workspace=workspace)
        try:
            raw = _parse_json(await _call(backends.author, system, brief))
            source = "model" if raw else "template"
        except Exception as exc:  # noqa: BLE001
            run.step("author_failed", error=str(exc)[:200])
            raw = None
    if raw is None:
        raw = fallback_storyboard(facts, brand, site=str(facts.get("site") or ""), media=pool)
        source = "template"
    board = normalize_storyboard(raw, templates=catalog, brand=brand, fmt=primary, fps=fps, login=login,
                                 fallback_title=str(facts.get("name") or ""), media=pool)
    run.step("storyboard", source=source, scenes=len(board["scenes"]))

    # 2-3. Capture + polish
    await _capture_and_polish(run, board)
    await _snap_durations(run, board, music)

    reports: list[dict[str, Any]] = []
    revisions: list[dict[str, Any]] = []
    assemble_options: dict[str, Any] = {}
    videos: dict[str, str] = {}
    narrated_for: str | None = None
    audio: dict[str, Any] = {}
    for round_no in range(max(0, max_revisions) + 1):
        atomic_write_text(root / "storyboard.json", json.dumps(board, indent=2, ensure_ascii=False) + "\n")
        # 4. Narration (only when the spoken lines or their timing changed)
        signature = json.dumps(narration_segments(board), sort_keys=True)
        if signature != narrated_for:
            audio = await _narrate(run, board, music)
            narrated_for = signature
        # 5-6. Render scenes (cached) and assemble every format
        clips = await _render_all(run, board)
        videos = {fmt: await _assemble_format(run, board, fmt, clips[fmt], audio, assemble_options) for fmt in picked}
        # 7. QA: full rubric on the primary cut, objective checks on the others
        merged: dict[str, Any] = {"findings": [], "verdict": "PASS"}
        rank = {"PASS": 0, "SKIPPED": 0, "WARN": 1, "BLOCK": 2}
        starts, total = timeline(board)
        timed = [{**scene, "start": starts[i]} for i, scene in enumerate(board["scenes"])]
        for fmt in picked:
            width, height = run.sizes[fmt]
            report = await run_video_qa(
                videos[fmt],
                scenes=timed,
                requirements={"width": width, "height": height, "fps": board["fps"], "format": fmt,
                              "duration": total},
                provider=backends.vision if fmt == primary else None,
                policy=backends.qa_policy,
                work_dir=root / "qa" / f"round-{round_no}" / fmt.replace(":", "x"),
                context={"brand": board["brand"], "format": fmt, "title": board["title"]},
                vision=fmt == primary,
            )
            report["format"] = fmt
            reports.append(report)
            merged["findings"] += [{**item, "format": fmt} for item in report.get("findings") or []]
            if rank.get(str(report.get("verdict")), 2) > rank.get(merged["verdict"], 0):
                merged["verdict"] = report.get("verdict")
        run.step("qa", round=round_no, verdict=merged["verdict"], findings=len(merged["findings"]))
        if merged["verdict"] in {"PASS", "SKIPPED"} or round_no >= max_revisions:
            break
        # 8. Revise: targeted scenes plus one mix fix
        new_board, changed = await revise_storyboard(board, merged, author=backends.author, templates=catalog,
                                                     round_no=round_no + 1, workspace=workspace, media=pool)
        global_checks = {item["check"] for item in merged["findings"]
                         if item.get("scene_index") is None and item.get("status") in {"WARN", "BLOCK"}}
        fixable = {"loudness", "true_peak", "silence"} | ({"audio"} if music else set())
        mix_fix = bool(global_checks & fixable) and not assemble_options.get("normalize_loudness")
        if mix_fix:
            assemble_options = {**assemble_options, "normalize_loudness": True}
        if not changed and not mix_fix:
            break
        recapture = {index for index in changed
                     if new_board["scenes"][index].get("template") == FOOTAGE
                     and (new_board["scenes"][index].get("props") or {}).get("steps")
                     != (board["scenes"][index].get("props") or {}).get("steps")}
        board = new_board
        if recapture:
            await _capture_and_polish(run, board, recapture)
        revisions.append({"round": round_no + 1, "scenes": changed, "mix": mix_fix})
        run.step("revise", round=round_no + 1, scenes=changed, mix=mix_fix)

    # 9. Deliverables
    atomic_write_text(root / "storyboard.json", json.dumps(board, indent=2, ensure_ascii=False) + "\n")
    stills = await build_stills(board, videos, root / "stills")
    final = reports[-len(picked):] if reports else []
    verdicts = [str(report.get("verdict")) for report in final]
    status = "delivered" if all(v in {"PASS", "SKIPPED", "WARN"} for v in verdicts) else "needs_review"
    manifest = {
        "schema_version": 1,
        "created_at": datetime.now(UTC).isoformat(),
        "status": status,
        "ok": bool(videos),
        "title": board["title"],
        "storyboard": str(root / "storyboard.json"),
        "storyboard_source": source,
        "videos": videos,
        "thumbnail": stills.get("thumbnail", ""),
        "posters": stills.get("posters", {}),
        "covers": stills.get("covers", {}),
        "captions": audio.get("captions_srt") or "",
        "qa": {
            "verdicts": dict(zip(picked, verdicts, strict=False)),
            "rounds": len(revisions),
            "reports": [str(Path(r["contact_sheet"]).parent / "video-qa.json") for r in final if r.get("contact_sheet")],
            "contact_sheets": [str(r["contact_sheet"]) for r in final if r.get("contact_sheet")],
            "open_findings": [
                {"format": report.get("format"),
                 **{k: item.get(k) for k in ("check", "status", "scene_index", "timestamp", "evidence")}}
                for report in final for item in report.get("findings") or []
                if item.get("status") in {"WARN", "BLOCK"}
            ][:40],
        },
        "revisions": revisions,
        "renders": run.renders,
        "log": run.log,
    }
    atomic_write_text(root / "manifest.json", json.dumps(manifest, indent=2, ensure_ascii=False, default=str) + "\n")
    return manifest


# ---------------------------------------------------------------------------
# Default backends (real Montage layers)
# ---------------------------------------------------------------------------


def _motion_module() -> Any | None:
    try:
        import navin.montage.motion as motion
    except Exception:  # noqa: BLE001 - renderer not installed in this build
        return None
    return motion if hasattr(motion, "render_motion") else None


def default_templates() -> list[Any]:
    motion = _motion_module()
    if motion is None:
        return list(MOTION_TEMPLATES)
    try:
        return list(motion.list_templates()) or list(MOTION_TEMPLATES)
    except Exception:  # noqa: BLE001
        return list(MOTION_TEMPLATES)


async def _render_footage(scene: dict[str, Any], output: str, *, width: int, height: int, fps: int) -> str:
    """Frame a captured take on the canvas: trim, cover-crop, constant fps."""
    from navin.montage.detect import find_ffmpeg
    from navin.montage.ffmpeg_runner import run_process

    ffmpeg = await asyncio.to_thread(find_ffmpeg)
    props = scene.get("props") or {}
    src = str(props.get("src") or "")
    if not ffmpeg or not src or not Path(src).is_file():
        raise DemoVideoError("footage scene has no captured take")
    vf = (
        f"scale={width}:{height}:force_original_aspect_ratio=increase,crop={width}:{height},"
        f"fps={fps},setsar=1,format=yuv420p"
    )
    result = await run_process(
        [ffmpeg, "-hide_banner", "-nostdin", "-y", "-ss", f"{float(props.get('trim_start') or 0):.3f}",
         "-i", src, "-t", f"{float(scene['duration']):.3f}", "-vf", vf, "-an",
         "-c:v", "libx264", "-preset", "medium", "-crf", "18", str(output)],
        timeout_s=600,
    )
    if not result.ok:
        raise DemoVideoError(result.stderr or "footage framing failed")
    return output


async def default_render(storyboard: dict[str, Any], output: str) -> str:
    """One scene through ``navin.montage.motion.render_motion`` (footage via ffmpeg)."""
    scene = storyboard["scenes"][0]
    width = int(storyboard.get("width") or FORMATS[storyboard["format"]][0])
    height = int(storyboard.get("height") or FORMATS[storyboard["format"]][1])
    fps = int(storyboard.get("fps") or 30)
    if scene["template"] == FOOTAGE:
        return await _render_footage(scene, output, width=width, height=height, fps=fps)
    motion = _motion_module()
    if motion is None:
        raise DemoVideoError("motion renderer is not installed")
    props = {k: v for k, v in (scene.get("props") or {}).items() if k not in {"revision", "fit", "motion"}}
    result = await motion.render_motion(
        output=output,
        template=scene["template"],
        props=props,
        brand=storyboard.get("brand") or None,
        settings=motion.RenderSettings(width=width, height=height, fps=fps, duration=float(scene["duration"])),
    )
    if not result.get("ok"):
        raise DemoVideoError(str(result.get("error") or "motion render failed")[:300])
    return str(result.get("output") or output)


def _assemble_transition(job: dict[str, Any]) -> str:
    styles = [item for item in job.get("transitions") or [] if item and item != "none"]
    return styles[0] if styles else "none"


async def default_assemble(job: dict[str, Any]) -> str:
    """Premium assemble over ``navin.montage.assemble`` (+ ASS captions)."""
    from navin.montage import assemble as assemble_mod

    subtitles: str | None = None
    fonts_dir: str | None = None
    segments = job.get("caption_segments") or []
    if job.get("captions") and segments:
        try:
            from navin.montage.captions import build_captions

            work = Path(str(job.get("work_dir") or Path(job["output"]).parent))
            caption = await asyncio.to_thread(
                build_captions,
                segments,
                work / f"captions-{str(job.get('format') or 'x').replace(':', 'x')}.ass",
                width=int(job["width"]),
                height=int(job["height"]),
                brand=_brand_kit_dict(job.get("brand")),
            )
            subtitles, fonts_dir = caption.path, caption.fonts_dir
        except Exception:  # noqa: BLE001 - the SRT sidecar still ships
            subtitles = job.get("captions_srt")
    music = job.get("music") if job.get("music") not in (None, "", "auto") else None
    kwargs: dict[str, Any] = {
        "visuals": list(job["clips"]),
        "output": str(job["output"]),
        "durations": [0.0] * len(job["clips"]),
        "music": music,
        "voice": None if job.get("voice_cues") else job.get("voice"),
        "subtitles": subtitles,
        "width": int(job["width"]),
        "height": int(job["height"]),
        "fps": int(job["fps"]),
        "transition": _assemble_transition(job),
        "transition_duration": float(job.get("transition_duration") or 0.4),
    }
    params = inspect.signature(assemble_mod.spec_from_paths).parameters
    for key, value in {
        "quality": str(job.get("quality") or "premium"),
        "voice_cues": job.get("voice_cues") or None,
        "fonts_dir": fonts_dir,
        "brand": _brand_kit_dict(job.get("brand")),
        "loudness": float(job.get("loudness_target") or -14.0) if job.get("normalize_loudness") else "auto",
    }.items():
        if key in params and value is not None:
            kwargs[key] = value
    spec = assemble_mod.spec_from_paths(**kwargs)
    result = await assemble_mod.run_assemble(spec)
    if not result.get("ok"):
        raise DemoVideoError(str(result.get("error") or "assemble failed")[:300])
    return str(result.get("output") or job["output"])


def _brand_kit_dict(brand: Any) -> dict[str, Any] | None:
    if not isinstance(brand, dict):
        return None
    try:
        from navin.montage.motion.brand import load_brand_kit

        return load_brand_kit(brand, use_marketing_store=False).to_dict()
    except Exception:  # noqa: BLE001 - captions fall back to their default style
        return dict(brand)


async def default_narrate(segments: list[dict[str, Any]], out_dir: Path) -> dict[str, Any] | None:
    """Segmented narration through ``generate_speech(segments=...)``."""
    from navin.agent.tools.speech_generation import SpeechGenerationTool
    from navin.config.loader import load_config
    from navin.config.paths import get_data_dir

    tool = SpeechGenerationTool(workspace=get_data_dir(), config=load_config().tools.speech_generation)
    raw = await tool.execute(segments=[segment["text"] for segment in segments])
    text = str(raw)
    try:
        payload = json.loads(text)
    except json.JSONDecodeError:
        raise DemoVideoError(text[:200] or "narration failed") from None
    rows = payload.get("segments") or []
    cues: list[dict[str, Any]] = []
    captions: list[dict[str, Any]] = []
    for segment, row in zip(segments, rows, strict=False):
        path = str(row.get("path") or "")
        if not path:
            continue
        start = float(segment["start"])
        cues.append({"path": path, "at": start})
        captions.append({"text": row.get("text") or segment["text"], "start": start,
                         "end": start + float(row.get("duration_s") or (segment["end"] - start)),
                         "words": row.get("words") or [], "words_relative": True})
    return {"voice_cues": cues, "caption_segments": captions or segments}


async def default_beats(music: str) -> list[float]:
    from navin.montage.audio import detect_beats

    return list((await detect_beats(music)).get("beats") or [])


async def default_capture(shot: dict[str, Any], out_dir: Path) -> dict[str, Any] | None:
    """Scripted hi-DPI capture through the ``browser`` tool.

    Login happens before ``record_start`` (saved ``secret_ref`` only, the
    session is kept), so no credential is ever on screen; the tool writes the
    ``<rec>.actions.json`` click/scroll log next to the recording.
    """
    from navin.agent.tools.browser import BrowserTool
    from navin.config.loader import load_config

    tool = BrowserTool(config=load_config().tools.browser)
    login = shot.get("login") or {}
    url = str(shot.get("url") or "")

    async def act(**kwargs: Any) -> str:
        result = await tool.execute(**kwargs)
        text = str(getattr(result, "content", result) or "")
        if text.startswith("Error"):
            raise DemoVideoError(text[:200])
        return text

    if login:
        await act(action="login", url=str(login.get("url") or url),
                  **{k: str(login[k]) for k in ("username_ref", "totp_ref") if login.get(k)},
                  **({"password_ref": str(login["secret_ref"])} if login.get("secret_ref") else {}))
    if url:
        await act(action="navigate", url=url)
    await act(action="record_start")
    try:
        for step in shot.get("steps") or []:
            kind = str(step.get("action") or "").lower()
            selector = step.get("selector")
            if kind in {"goto", "navigate"} and (step.get("url") or url):
                await act(action="navigate", url=str(step.get("url") or url))
            elif kind == "click" and selector:
                await act(action="click", selector=str(selector))
            elif kind == "hover" and selector:
                await act(action="hover", selector=str(selector))
            elif kind == "type" and selector:
                payload = {"secret_ref": str(step["secret_ref"])} if step.get("secret_ref") else {"text": str(step.get("text") or "")}
                await act(action="type", selector=str(selector), **payload)
            elif kind == "press" and step.get("key"):
                await act(action="press_key", key=str(step["key"]))
            elif kind == "scroll":
                await act(action="scroll", direction=str(step.get("direction") or "down"))
            elif kind == "wait":
                await act(action="wait", ms=int(step.get("ms") or 800))
    finally:
        out_dir.mkdir(parents=True, exist_ok=True)
        stopped = await act(action="record_stop", output=str(out_dir / f"{shot['id']}.webm"))
    found: dict[str, str] = {}
    for line in stopped.splitlines():
        label, _, value = line.strip().partition(":")
        if label in {"mp4", "webm", "actions"} and value.strip():
            found[label] = value.strip()
    video = found.get("mp4") or found.get("webm")
    return {"video": video, "actions": found.get("actions")} if video else None


def _default_polish_fn() -> Backend | None:
    """``navin.montage.footage.polish_footage`` (montage footage_polish)."""
    try:
        from navin.montage.footage import polish_footage
    except Exception:  # noqa: BLE001 - build without the footage polisher
        return None
    return polish_footage


async def default_polish(
    *, video: str, actions: str | None, format: str, output: str, options: dict[str, Any]
) -> str | None:
    """Auto zoom/pan on the actions, cursor, dead-time trim, smart crop per format."""
    fn = _default_polish_fn()
    if fn is None:
        return None
    from navin.montage.footage import FootageOptions

    width, height = (options.get("size") or FORMATS.get(format) or (1920, 1080))[:2]
    footage_options = FootageOptions.from_mapping(
        {
            "layout": "crop",
            "title": str(options.get("caption") or "")[:80],
            "intensity": 1.2 if options.get("motion") == "push_in" else None,
            "cursor": "auto",
        }
    )
    Path(output).parent.mkdir(parents=True, exist_ok=True)
    result = await asyncio.to_thread(
        fn, video, actions, output, options=footage_options, width=int(width), height=int(height)
    )
    if isinstance(result, dict):
        if result.get("ok") is False:
            raise DemoVideoError(str(result.get("error") or "footage polish failed")[:200])
        return str(result.get("output") or "") or None
    return str(result) if result else None


def default_vision_provider() -> Any | None:
    try:
        from navin.config.loader import load_config
        from navin.providers.factory import load_provider_snapshot
        from navin.providers.visual_qa import get_visual_qa_provider, visual_qa_credentials_ready
        from navin.utils.llm_runtime import runtime_from_provider_snapshot

        config = load_config().tools.visual_qa
        runtime = runtime_from_provider_snapshot(load_provider_snapshot(preset_name=config.preset or None))
        factory = get_visual_qa_provider(config.provider)
        if factory is None or not visual_qa_credentials_ready(runtime):
            return None
        return factory(runtime=runtime, provider_name=config.provider)
    except Exception:  # noqa: BLE001 - objective QA still runs
        return None


def default_author(settings: dict[str, Any] | None = None) -> Backend | None:
    from navin.marketing import ai

    if not ai.enabled(settings):
        return None

    def _author(system: str, user: str) -> Any:
        from navin.desk_ai import ask_json

        data, _model = ask_json(
            ai.MARKETING_TASK_ROLES["launch"], system, user,
            env_flag=ai.ENV_FLAG, profile=settings, max_tokens=2400, temperature=0.4, timeout_s=180.0,
        )
        return data if isinstance(data, dict) else None

    return _author


def _tts_ready() -> bool:
    try:
        from navin.audio.tts import resolve_tts_config
        from navin.config.loader import load_config

        return bool(resolve_tts_config(load_config()).configured)
    except Exception:  # noqa: BLE001
        return False


def _browser_ready() -> bool:
    try:
        from navin.config.loader import load_config

        return bool(getattr(load_config().tools.browser, "enabled", True))
    except Exception:  # noqa: BLE001
        return False


def pipeline_readiness() -> dict[str, Any]:
    from navin.montage.detect import find_ffmpeg

    ffmpeg = bool(find_ffmpeg())
    renderer = _motion_module() is not None
    missing = [name for name, ok in (("ffmpeg", ffmpeg), ("motion renderer", renderer)) if not ok]
    return {
        "ready": not missing,
        "ffmpeg": ffmpeg,
        "renderer": renderer,
        "polish": _default_polish_fn() is not None,
        "missing": missing,
    }


def default_backends(settings: dict[str, Any] | None = None) -> DemoVideoBackends:
    return DemoVideoBackends(
        render=default_render if _motion_module() is not None else None,
        assemble=default_assemble,
        templates=default_templates,
        author=default_author(settings),
        capture=default_capture if _browser_ready() else None,
        polish=default_polish if _default_polish_fn() is not None else None,
        narrate=default_narrate if _tts_ready() else None,
        beats=default_beats,
        vision=default_vision_provider(),
    )


def _store_media(store: Any) -> list[str]:
    from navin.marketing.assets import resolve_asset

    out: list[str] = []
    for item in store.load_harvest().get("images") or []:
        if not isinstance(item, dict):
            continue
        for candidate in (str(item.get("path") or ""), str(resolve_asset(store, str(item.get("name") or "")) or "")):
            if candidate and Path(candidate).is_file() and Path(candidate).suffix.lower() in _MEDIA_SUFFIXES:
                out.append(candidate)
                break
    return list(dict.fromkeys(out))[:8]


def produce_demo_video(
    store: Any,
    *,
    backends: DemoVideoBackends | None = None,
    formats: Sequence[str] = DEFAULT_FORMATS,
    max_revisions: int = MAX_REVISIONS,
) -> dict[str, Any]:
    """Marketing desk entry point: run the pipeline for the bound product."""
    from navin.marketing.content import product_facts

    settings = store.load_settings()
    active = backends or default_backends(settings)
    facts = dict(product_facts(store))
    brand = brand_block(facts, store.load_brand(), store.load_harvest())
    product = store.load_product()
    login = product.get("demo_login") or settings.get("demo_login") or {}
    stamp = datetime.now(UTC).strftime("%Y%m%d-%H%M%S")
    workspace = product.get("workspace")
    music = str(settings.get("demo_music") or "")
    return asyncio.run(
        run_demo_video(
            brief=demo_video_brief(store),
            facts=facts,
            brand=brand,
            out_dir=store.root / "demo-video" / stamp,
            backends=active,
            formats=formats,
            login={k: v for k, v in login.items() if k in _LOGIN_KEYS} if isinstance(login, dict) else None,
            music=music if music and Path(music).is_file() else None,
            max_revisions=max_revisions,
            workspace=Path(workspace) if workspace else None,
            media=_store_media(store),
        )
    )
