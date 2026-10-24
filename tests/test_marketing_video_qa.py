# Copyright (c) 2026-present Navinspire IA
# SPDX-License-Identifier: AGPL-3.0-only

"""Objective and vision video QA on synthetic clips."""

from __future__ import annotations

import asyncio
import shutil
import subprocess
from pathlib import Path

import pytest

from navin.marketing.video_qa import (
    failing_scenes,
    parse_filter_log,
    run_video_qa,
    sample_timestamps,
    scene_at,
    scene_starts,
)
from navin.providers.visual_qa import (
    VIDEO_DIMENSIONS,
    VisualQAProviderError,
    VisualQAProviderResult,
    normalize_video_analysis,
)

pytestmark = pytest.mark.skipif(
    shutil.which("ffmpeg") is None or shutil.which("ffprobe") is None,
    reason="ffmpeg/ffprobe not installed",
)


def _ffmpeg(*args: str) -> None:
    subprocess.run(["ffmpeg", "-hide_banner", "-loglevel", "error", "-y", *args], check=True)


def _black_then_motion(path: Path) -> Path:
    """1.5 s black (scene 0) then 2.5 s moving test pattern with a loud tone."""
    _ffmpeg(
        "-f", "lavfi", "-i", "color=c=black:s=320x240:d=1.5:r=30",
        "-f", "lavfi", "-i", "testsrc2=s=320x240:d=2.5:r=30",
        "-f", "lavfi", "-i", "sine=frequency=440:sample_rate=44100:duration=4",
        "-filter_complex", "[0:v][1:v]concat=n=2:v=1[v]",
        "-map", "[v]", "-map", "2:a", "-t", "4",
        "-c:v", "libx264", "-pix_fmt", "yuv420p", "-c:a", "aac", str(path),
    )
    return path


def _frozen_and_silent(path: Path) -> Path:
    """1 s of motion, then a 3 s frozen frame, over pure silence."""
    _ffmpeg(
        "-f", "lavfi", "-i", "testsrc2=s=320x240:d=1:r=30",
        "-f", "lavfi", "-i", "anullsrc=r=44100:cl=mono",
        "-filter_complex", "[0:v]tpad=stop_mode=clone:stop_duration=3[v]",
        "-map", "[v]", "-map", "1:a", "-t", "4",
        "-c:v", "libx264", "-pix_fmt", "yuv420p", "-c:a", "aac", str(path),
    )
    return path


def _dimension(status: str = "PASS", score: int = 92) -> dict:
    return {"status": status, "score": score, "evidence": ["visible fact"], "recommendation": ""}


class FakeVideoVision:
    def __init__(self, issues: list[dict] | None = None) -> None:
        self.calls: list[dict] = []
        self.issues = issues or []

    async def analyze_video(self, *, frames, contact_sheet, context):
        self.calls.append({"frames": frames, "contact_sheet": contact_sheet, "context": context})
        analysis = normalize_video_analysis(
            {"dimensions": {name: _dimension() for name in VIDEO_DIMENSIONS}, "issues": self.issues},
            frame_count=len(frames),
        )
        return VisualQAProviderResult(analysis=analysis, provider="fake", model="fake-vision", usage={})


def test_black_frames_detected_and_pinned_to_scene(tmp_path):
    clip = _black_then_motion(tmp_path / "black.mp4")
    report = asyncio.run(
        run_video_qa(
            clip,
            scenes=[{"template": "hero_intro", "duration": 1.5}, {"template": "footage", "duration": 2.5}],
            requirements={"width": 320, "height": 240, "fps": 30},
            work_dir=tmp_path / "qa",
            vision=False,
        )
    )
    black = [item for item in report["findings"] if item["check"] == "black_frames"]
    assert black and black[0]["status"] == "BLOCK"
    assert black[0]["scene_index"] == 0
    assert black[0]["timestamp"] == 0.0
    assert report["verdict"] == "BLOCK"
    assert 0 in failing_scenes(report)
    assert {"resolution", "fps", "duration"} <= {item["check"] for item in report["findings"]}
    assert all(item["status"] == "PASS" for item in report["findings"] if item["check"] in {"resolution", "fps"})
    assert (tmp_path / "qa" / "video-qa.json").is_file()
    assert Path(report["contact_sheet"]).is_file()
    assert report["frames"] and report["frames"][0]["scene_index"] == 0


def test_frozen_frames_and_silence_detected(tmp_path):
    clip = _frozen_and_silent(tmp_path / "frozen.mp4")
    report = asyncio.run(
        run_video_qa(
            clip,
            scenes=[{"duration": 1.0}, {"duration": 3.0}],
            requirements={"width": 320, "height": 240},
            work_dir=tmp_path / "qa",
            vision=False,
        )
    )
    checks = {item["check"]: item for item in report["findings"]}
    assert "frozen_frames" in checks
    frozen = checks["frozen_frames"]
    assert frozen["scene_index"] == 1 and frozen["timestamp"] >= 0.9
    assert "silence" in checks and checks["silence"]["status"] == "BLOCK"
    assert "loudness" in checks and checks["loudness"]["status"] == "WARN"
    assert report["verdict"] == "BLOCK"


def test_wrong_resolution_blocks(tmp_path):
    clip = _black_then_motion(tmp_path / "small.mp4")
    report = asyncio.run(
        run_video_qa(clip, requirements={"width": 1920, "height": 1080, "format": "16:9"},
                     work_dir=tmp_path / "qa", vision=False)
    )
    resolution = next(item for item in report["findings"] if item["check"] == "resolution")
    assert resolution["status"] == "BLOCK"


def test_vision_rubric_issue_maps_to_scene_and_timestamp(tmp_path):
    clip = _black_then_motion(tmp_path / "v.mp4")
    vision = FakeVideoVision(
        issues=[{"frame": 2, "check": "secrets_visible", "status": "BLOCK", "evidence": "password field shows text"}]
    )
    report = asyncio.run(
        run_video_qa(
            clip,
            scenes=[{"duration": 1.5}, {"duration": 2.5}],
            provider=vision,
            work_dir=tmp_path / "qa",
        )
    )
    assert vision.calls and vision.calls[0]["contact_sheet"].is_file()
    frames = vision.calls[0]["frames"]
    secret = next(item for item in report["findings"] if item["check"] == "secrets_visible" and item["timestamp"] is not None)
    assert secret["status"] == "BLOCK" and secret["source"] == "vision"
    assert secret["scene_index"] == frames[1]["scene_index"]
    assert secret["timestamp"] == pytest.approx(frames[1]["timestamp"])
    assert report["provider"]["model"] == "fake-vision"


def test_vision_failure_is_never_a_pass(tmp_path):
    class Broken:
        async def analyze_video(self, **_kwargs):
            raise VisualQAProviderError("garbage")

    clip = _black_then_motion(tmp_path / "b.mp4")
    report = asyncio.run(run_video_qa(clip, provider=Broken(), work_dir=tmp_path / "qa"))
    gate = next(item for item in report["findings"] if item["check"] == "vision_analysis")
    assert gate["status"] in {"WARN", "BLOCK"}
    assert report["verdict"] != "PASS"


def test_normalize_video_analysis_drops_bad_issues():
    with pytest.raises(VisualQAProviderError):
        normalize_video_analysis({"dimensions": {}}, frame_count=2)
    analysis = normalize_video_analysis(
        {
            "dimensions": {name: _dimension() for name in VIDEO_DIMENSIONS},
            "issues": [
                {"frame": 9, "check": "pacing", "status": "WARN", "evidence": "x"},
                {"frame": 1, "check": "made_up", "status": "WARN", "evidence": "x"},
                {"frame": 1, "check": "pacing", "status": "PASS", "evidence": "x"},
                {"frame": 1, "check": "pacing", "status": "WARN", "evidence": "scene drags"},
            ],
        },
        frame_count=2,
    )
    assert [item["evidence"] for item in analysis["issues"]] == ["scene drags"]


def test_sampling_hits_every_scene_boundary():
    starts = scene_starts([{"duration": 3}, {"duration": 2.5}, {"duration": 4}])
    assert starts == [0.0, 3.0, 5.5]
    stamps = sample_timestamps(9.5, starts, every_s=2.0)
    for start in starts:
        assert any(start <= stamp <= start + 0.3 for stamp in stamps)
    assert scene_at(starts, 5.6) == 2
    assert all(b - a >= 0.3 for a, b in zip(stamps, stamps[1:]))


def test_parse_filter_log_reads_ebur128_summary():
    log = (
        "[blackdetect @ 0x1] black_start:0 black_end:1.2 black_duration:1.2\n"
        "[freezedetect @ 0x2] lavfi.freezedetect.freeze_start: 2.0\n"
        "[freezedetect @ 0x2] lavfi.freezedetect.freeze_end: 4.5\n"
        "[silencedetect @ 0x3] silence_start: 1\n"
        "[silencedetect @ 0x3] silence_end: 3 | silence_duration: 2\n"
        "[Parsed_cropdetect_2 @ 0x4] crop=1920:800:0:140\n"
        "[Parsed_ebur128_1 @ 0x5] Summary:\n\n  Integrated loudness:\n    I:         -15.2 LUFS\n"
        "    Threshold: -25.0 LUFS\n\n  Loudness range:\n    LRA:         4.1 LU\n\n"
        "  True peak:\n    Peak:        -0.4 dBFS\n"
    )
    events = parse_filter_log(log, duration=6.0)
    assert events["black"][0]["duration"] == 1.2
    assert events["freezes"][0] == {"start": 2.0, "end": 4.5, "duration": 2.5}
    assert events["silences"][0]["duration"] == 2.0
    assert events["crop"] == [1920, 800, 0, 140]
    assert events["loudness"] == {"integrated": -15.2, "lra": 4.1, "true_peak": -0.4}
