# Copyright (c) 2026-present Navinspire IA
# SPDX-License-Identifier: AGPL-3.0-only

"""Demo-video pipeline: storyboard, capture, polish, render, assemble, QA, revise."""

from __future__ import annotations

import asyncio
import json
import shutil
import subprocess
from pathlib import Path
from unittest.mock import patch

import pytest

from navin.marketing.demo_video import (
    FOOTAGE,
    MOTION_TEMPLATES,
    DemoVideoBackends,
    capture_shot_list,
    default_assemble,
    normalize_storyboard,
    run_demo_video,
)
from navin.marketing.store import MarketingStore
from navin.marketing.video_qa import VideoQAPolicy
from navin.providers.visual_qa import (
    VIDEO_DIMENSIONS,
    VisualQAProviderResult,
    normalize_video_analysis,
)

needs_ffmpeg = pytest.mark.skipif(
    shutil.which("ffmpeg") is None or shutil.which("ffprobe") is None,
    reason="ffmpeg/ffprobe not installed",
)

SIZES = {"16:9": (320, 180), "9:16": (180, 320), "1:1": (240, 240)}
BRAND = {"name": "Acme", "colors": ["#101820", "#38bdf8"], "font": "Inter", "logo": ""}
FACTS = {"name": "Acme", "one_liner": "Acme closes deals faster", "pain": "manual CRM updates",
         "value": "CRM that updates itself", "cta": "Start free", "site": "https://acme.test"}


def _ffmpeg(*args: str) -> None:
    subprocess.run(["ffmpeg", "-hide_banner", "-loglevel", "error", "-y", *args], check=True)


def _storyboard() -> dict:
    return {
        "version": 1,
        "format": "16:9",
        "fps": 30,
        "brand": BRAND,
        "scenes": [
            {"template": "kinetic_title", "duration": 2, "props": {"title": "Stop typing into your CRM"},
             "narration": "Stop typing into your CRM \u2014 ever."},
            {"template": FOOTAGE, "duration": 3,
             "props": {"url": "https://acme.test/app", "steps": [{"action": "click", "selector": "#deal"}],
                       "login": {"secret_ref": "acme_demo", "password": "hunter2"}}},
            {"template": "feature_callout", "duration": 2.5, "props": {"label": "Deals update themselves"}},
            {"template": "cta_end_card", "duration": 2, "props": {"title": "Acme", "cta": "Start free"}},
        ],
        "audio": {"music": "auto", "voiceover": True, "captions": True, "snap_to_beats": True},
    }


class Fakes:
    """Records every call; the black scene is fixed by the first revision."""

    def __init__(self, tmp: Path, *, black_scene: str | None = None) -> None:
        self.tmp = tmp
        self.black_scene = black_scene
        self.renders: list[tuple[str, str, int]] = []
        self.captures: list[dict] = []
        self.polished: list[str] = []
        self.assembled: list[dict] = []
        self.narrated: list[list] = []
        self.authored: list[str] = []

    def author(self, system: str, user: str):
        self.authored.append(system)
        if "QA findings" in system:
            return None  # revise falls back to deterministic fixes
        return _storyboard()

    def capture(self, shot: dict, out_dir: Path) -> dict:
        self.captures.append(shot)
        out_dir.mkdir(parents=True, exist_ok=True)
        video = out_dir / f"{shot['id']}.mp4"
        _ffmpeg("-f", "lavfi", "-i", "testsrc2=s=640x360:d=3:r=30", "-c:v", "libx264", "-pix_fmt", "yuv420p", str(video))
        actions = out_dir / f"{shot['id']}.actions.json"
        actions.write_text(json.dumps([{"t": 0.5, "type": "click", "x": 10, "y": 10}]), encoding="utf-8")
        return {"video": str(video), "actions": str(actions)}

    def polish(self, *, video, actions, format, output, options):
        assert Path(actions).is_file()
        Path(output).parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(video, output)
        self.polished.append(format)
        return output

    def render(self, storyboard: dict, output: str) -> str:
        scene = storyboard["scenes"][0]
        width, height = storyboard["width"], storyboard["height"]
        assert (width, height) == SIZES[storyboard["format"]]
        self.renders.append((storyboard["format"], scene["template"], int((scene.get("props") or {}).get("revision") or 0)))
        black = scene["template"] == self.black_scene and not (scene.get("props") or {}).get("revision")
        source = f"color=c=black:s={width}x{height}" if black else f"testsrc2=s={width}x{height}"
        _ffmpeg("-f", "lavfi", "-i", f"{source}:d={scene['duration']}:r={storyboard['fps']}",
                "-c:v", "libx264", "-pix_fmt", "yuv420p", output)
        return output

    def narrate(self, segments: list, out_dir: Path) -> dict:
        self.narrated.append(segments)
        out_dir.mkdir(parents=True, exist_ok=True)
        voice = out_dir / "voice.m4a"
        _ffmpeg("-f", "lavfi", "-i", "sine=frequency=330:duration=9", "-c:a", "aac", str(voice))
        return {"voice": str(voice)}

    async def assemble(self, job: dict) -> str:
        assert job["quality"] == "premium"
        self.assembled.append(job)
        listing = Path(job["output"]).with_suffix(".txt")
        listing.write_text("".join(f"file '{clip}'\n" for clip in job["clips"]), encoding="utf-8")
        total = sum(job["durations"])
        _ffmpeg("-f", "concat", "-safe", "0", "-i", str(listing),
                "-f", "lavfi", "-i", f"sine=frequency=440:duration={total}",
                "-map", "0:v", "-map", "1:a", "-c:v", "copy", "-c:a", "aac", "-shortest", job["output"])
        return job["output"]


class FakeVision:
    def __init__(self) -> None:
        self.calls = 0

    async def analyze_video(self, *, frames, contact_sheet, context):
        self.calls += 1
        dims = {name: {"status": "PASS", "score": 95, "evidence": ["clean frame"], "recommendation": ""}
                for name in VIDEO_DIMENSIONS}
        return VisualQAProviderResult(
            analysis=normalize_video_analysis({"dimensions": dims, "issues": []}, frame_count=len(frames)),
            provider="fake", model="fake-vision", usage={},
        )


def _screenshot(tmp: Path) -> str:
    from PIL import Image

    path = tmp / "screen.png"
    Image.new("RGB", (64, 40), (40, 120, 200)).save(path)
    return str(path)


def _backends(fakes: Fakes, vision: FakeVision) -> DemoVideoBackends:
    return DemoVideoBackends(
        render=fakes.render,
        assemble=fakes.assemble,
        templates=lambda: list(MOTION_TEMPLATES),
        author=fakes.author,
        capture=fakes.capture,
        polish=fakes.polish,
        narrate=fakes.narrate,
        vision=vision,
        # Fake audio is a raw tone: loudness is not what these tests measure.
        qa_policy=VideoQAPolicy(lufs_tolerance=40.0, max_true_peak_db=6.0),
    )


def test_normalize_storyboard_enforces_craft_and_strips_secrets():
    board = normalize_storyboard(_storyboard(), templates=MOTION_TEMPLATES, brand=BRAND, media=[])
    templates = [scene["template"] for scene in board["scenes"]]
    assert templates[0] == "kinetic_title"
    assert any(name in {"device_mockup_3d", "hero_intro"} for name in templates)
    assert templates[-1] == "cta_end_card"
    footage = next(scene for scene in board["scenes"] if scene["template"] == FOOTAGE)
    assert footage["props"]["login"] == {"secret_ref": "acme_demo"}
    assert "hunter2" not in json.dumps(board)
    assert "\u2014" not in json.dumps(board, ensure_ascii=False)
    assert all(2 <= scene["duration"] <= 6 for scene in board["scenes"])
    shots = capture_shot_list(board)
    assert shots[0]["login"] == {"secret_ref": "acme_demo"}
    assert shots[0]["steps"] == [{"action": "click", "selector": "#deal"}]


def test_unknown_templates_are_dropped_and_hook_added():
    board = normalize_storyboard(
        {"scenes": [{"template": "made_up", "duration": 9},
                    {"template": "stats_counter", "duration": 9, "props": {"stats": [{"value": 3, "label": "x faster"}]}},
                    {"template": "feature_callout", "duration": 3, "props": {"label": "No media here"}},
                    {"template": "cta_end_card", "duration": 3, "props": {"title": "Acme", "cta": "Start free now today"}}]},
        templates=MOTION_TEMPLATES, brand=BRAND,
    )
    names = [scene["template"] for scene in board["scenes"]]
    assert "made_up" not in names
    assert names[0] == "hero_intro"
    assert next(s for s in board["scenes"] if s["template"] == "stats_counter")["duration"] == 4.0
    # A media template without media becomes a scene that needs none.
    assert "feature_callout" not in names
    assert any(s["props"].get("title") == "No media here" for s in board["scenes"] if s["template"] == "gradient_stage")
    cta = board["scenes"][-1]["props"]
    assert cta["headline"] == "Acme" and cta["button"] == "Start free now today" and "title" not in cta


@needs_ffmpeg
def test_pipeline_happy_path_delivers_three_formats_and_stills(tmp_path):
    fakes, vision = Fakes(tmp_path), FakeVision()
    manifest = asyncio.run(
        run_demo_video(
            brief="Demo video brief: show Acme updating a deal by itself.",
            facts=FACTS, brand=BRAND, out_dir=tmp_path / "run", backends=_backends(fakes, vision),
            sizes=SIZES, login={"secret_ref": "acme_demo"}, media=[_screenshot(tmp_path)],
        )
    )
    assert manifest["ok"] and manifest["status"] == "delivered"
    assert set(manifest["videos"]) == {"16:9", "9:16", "1:1"}
    for path in manifest["videos"].values():
        assert Path(path).is_file()
    assert Path(manifest["thumbnail"]).is_file()
    assert set(manifest["posters"]) == {"16:9", "9:16", "1:1"}
    assert set(manifest["covers"]) == {"landscape", "vertical", "square"}
    assert manifest["storyboard_source"] == "model"
    assert manifest["revisions"] == []
    # The capture got the secret reference, never a secret value.
    assert fakes.captures and fakes.captures[0]["login"] == {"secret_ref": "acme_demo"}
    assert sorted(fakes.polished) == ["16:9", "1:1", "9:16"]
    # Three.js is always in the film.
    rendered = {template for _fmt, template, _rev in fakes.renders}
    assert rendered & {"device_mockup_3d", "hero_intro"}
    assert vision.calls == 1
    assert fakes.narrated and fakes.assembled[0]["voice"]
    assert Path(manifest["captions"]).read_text(encoding="utf-8").startswith("1\n")
    assert Path(manifest["storyboard"]).is_file()
    assert (tmp_path / "run" / "manifest.json").is_file()


@needs_ffmpeg
def test_qa_failure_rerenders_only_the_failing_scene(tmp_path):
    fakes, vision = Fakes(tmp_path, black_scene="feature_callout"), FakeVision()
    manifest = asyncio.run(
        run_demo_video(
            brief="brief", facts=FACTS, brand=BRAND, out_dir=tmp_path / "run",
            backends=_backends(fakes, vision), formats=("16:9",), sizes=SIZES, media=[_screenshot(tmp_path)],
        )
    )
    assert len(manifest["revisions"]) == 1
    fixed = manifest["revisions"][0]["scenes"]
    board = json.loads(Path(manifest["storyboard"]).read_text(encoding="utf-8"))
    assert [board["scenes"][i]["template"] for i in fixed] == ["feature_callout"]
    second_pass = [entry for entry in fakes.renders if entry[2] == 1]
    assert second_pass == [("16:9", "feature_callout", 1)]
    first_pass = [entry for entry in fakes.renders if entry[2] == 0]
    assert len(first_pass) == len(board["scenes"])
    assert manifest["qa"]["verdicts"]["16:9"] in {"PASS", "WARN"}
    assert len(fakes.assembled) == 2


@needs_ffmpeg
def test_revise_stops_after_two_rounds(tmp_path):
    class Stubborn(Fakes):
        def render(self, storyboard, output):
            scene = storyboard["scenes"][0]
            width, height = storyboard["width"], storyboard["height"]
            source = f"color=c=black:s={width}x{height}" if scene["template"] == "feature_callout" else f"testsrc2=s={width}x{height}"
            self.renders.append((storyboard["format"], scene["template"], int((scene.get("props") or {}).get("revision") or 0)))
            _ffmpeg("-f", "lavfi", "-i", f"{source}:d={scene['duration']}:r=30", "-c:v", "libx264", "-pix_fmt", "yuv420p", output)
            return output

    fakes, vision = Stubborn(tmp_path), FakeVision()
    manifest = asyncio.run(
        run_demo_video(brief="brief", facts=FACTS, brand=BRAND, out_dir=tmp_path / "run",
                       backends=_backends(fakes, vision), formats=("16:9",), sizes=SIZES,
                       media=[_screenshot(tmp_path)])
    )
    assert len(manifest["revisions"]) == 2
    assert manifest["status"] == "needs_review"
    assert any(item["check"] == "black_frames" for item in manifest["qa"]["open_findings"])


@needs_ffmpeg
def test_capture_unavailable_falls_back_to_3d_mockup(tmp_path):
    fakes, vision = Fakes(tmp_path), FakeVision()
    backends = _backends(fakes, vision)
    backends.capture = None
    manifest = asyncio.run(
        run_demo_video(brief="brief", facts=FACTS, brand=BRAND, out_dir=tmp_path / "run",
                       backends=backends, formats=("16:9",), sizes=SIZES)
    )
    board = json.loads(Path(manifest["storyboard"]).read_text(encoding="utf-8"))
    assert FOOTAGE not in [scene["template"] for scene in board["scenes"]]
    assert any(entry["step"] == "capture_fallback" for entry in manifest["log"])


@needs_ffmpeg
def test_default_assemble_runs_the_real_montage_assembler(tmp_path):
    clips = []
    for index in range(2):
        clip = tmp_path / f"c{index}.mp4"
        _ffmpeg("-f", "lavfi", "-i", "testsrc2=s=320x180:d=1:r=30", "-c:v", "libx264", "-pix_fmt", "yuv420p", str(clip))
        clips.append(str(clip))
    out = asyncio.run(
        default_assemble({"clips": clips, "durations": [1, 1], "transitions": ["fade", "fade"],
                          "transition_duration": 0.3, "width": 320, "height": 180, "fps": 30,
                          "output": str(tmp_path / "final.mp4"), "quality": "premium"})
    )
    assert Path(out).is_file() and Path(out).stat().st_size > 1000


def test_produce_video_runs_the_pipeline_and_stores_an_assembled_video(tmp_path):
    from navin.marketing import produce

    store = MarketingStore(tmp_path)
    store.save_product({"name": "Acme", "one_liner": "CRM", "site": "https://acme.test"})
    videos = {}
    for fmt in ("16:9", "9:16", "1:1"):
        path = tmp_path / f"demo-{fmt.replace(':', 'x')}.mp4"
        path.write_bytes(b"\x00\x00\x00\x18ftypmp42" + b"0" * 64)
        videos[fmt] = str(path)
    thumb = tmp_path / "thumb.jpg"
    thumb.write_bytes(b"\xff\xd8\xff" + b"0" * 32)
    manifest = {"ok": True, "status": "delivered", "videos": videos, "thumbnail": str(thumb),
                "posters": {}, "covers": {}, "qa": {"verdicts": {"16:9": "PASS"}, "rounds": 0},
                "storyboard": "", "title": "Acme demo"}
    with patch.object(produce, "_demo_video_ready", return_value=""), \
         patch("navin.marketing.demo_video.produce_demo_video", return_value=manifest) as run:
        result = produce.produce_assets(store, kinds=["video"], generate=True)
    assert run.call_count == 1  # one premium run feeds every clip placement
    assert result["produced"] == 4
    clips = [row for row in store.load_creatives() if row.get("kind") == "video"]
    assert clips and all(row["status"] == "produced" for row in clips)
    assert all(row["pipeline"] == "demo_video" and row["assembled"] for row in clips)
    by_placement = {row["placement"]: row for row in clips}
    assert by_placement["instagram reel"]["format"] == "9:16"
    assert by_placement["facebook clip"]["format"] == "1:1"
    assert by_placement["linkedin clip"]["format"] == "16:9"
    for row in clips:
        assert Path(row["path"]).is_file()
        assert row["thumbnail"]


def test_produce_video_skips_with_reason_when_pipeline_not_ready(tmp_path):
    from navin.marketing import produce

    store = MarketingStore(tmp_path)
    store.save_product({"name": "Acme", "one_liner": "CRM"})
    with patch.object(produce, "_demo_video_ready", return_value="motion renderer is not installed"):
        result = produce.produce_assets(store, kinds=["video"], generate=True)
    assert result["produced"] == 0
    assert any("motion renderer" in item for item in result["skipped"])


def test_default_polish_drives_footage_polish_per_format(tmp_path):
    from navin.marketing import demo_video

    calls = []

    def fake_polish(source, actions, output, *, options, width, height):
        calls.append((source, actions, output, options.layout, width, height))
        Path(output).write_bytes(b"0")
        return {"ok": True, "output": output}

    with patch.object(demo_video, "_default_polish_fn", return_value=fake_polish):
        out = asyncio.run(
            demo_video.default_polish(
                video="rec.mp4", actions="rec.actions.json", format="9:16",
                output=str(tmp_path / "p" / "rec-9x16.mp4"), options={"size": [1080, 1920], "caption": "Create a deal"},
            )
        )
    assert out == str(tmp_path / "p" / "rec-9x16.mp4")
    assert calls == [("rec.mp4", "rec.actions.json", out, "crop", 1080, 1920)]


def test_default_render_rejects_footage_without_take(tmp_path):
    from navin.marketing.demo_video import DemoVideoError, default_render

    board = {"format": "16:9", "fps": 30, "width": 320, "height": 180,
             "scenes": [{"template": FOOTAGE, "duration": 3, "props": {}}]}
    with pytest.raises(DemoVideoError):
        asyncio.run(default_render(board, str(tmp_path / "x.mp4")))
