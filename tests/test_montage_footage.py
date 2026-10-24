# Copyright (c) 2026-present Navinspire IA
# SPDX-License-Identifier: AGPL-3.0-only

"""Polished footage: camera planner, cursor, dead-time trim, smart crop."""

from __future__ import annotations

import asyncio
import json
import math
import re
import shutil
import subprocess
from pathlib import Path
from unittest.mock import patch

import pytest

from navin.montage.footage import (
    ActionEvent,
    CameraOptions,
    DeadSegment,
    FootageOptions,
    TimeRemap,
    actions_from_dict,
    find_actions_sidecar,
    plan_camera,
    plan_cursor,
    plan_dead_segments,
    polish_footage,
    prepare_footage,
    remap_actions,
    remap_srt,
    render_footage,
)

FFMPEG = shutil.which("ffmpeg")
FFPROBE = shutil.which("ffprobe")
needs_ffmpeg = pytest.mark.skipif(not (FFMPEG and FFPROBE), reason="ffmpeg/ffprobe missing")

# Synthetic recording: viewport 320x180 css at DPR 2 -> 640x360 pixels, 30 fps,
# 8 s. Grey page with a moving red box, frozen between 2 s and 6 s, a green
# "Save" button far right (css 280,90) and a blue badge top-left from 6 s on.
VIEW_W, VIEW_H, DPR, FPS, DURATION = 320, 180, 2, 30, 8.0
CLICK_1 = (60.0, 45.0)
CLICK_2 = (280.0, 90.0)
ACTIONS = {
    "version": 1,
    "viewport": {"width": VIEW_W, "height": VIEW_H},
    "device_scale_factor": DPR,
    "fps": FPS,
    "duration": DURATION,
    "events": [
        {"t": 0.6, "type": "hover", "x": 40, "y": 40, "bbox": None, "label": ""},
        {
            "t": 1.2,
            "type": "click",
            "x": CLICK_1[0],
            "y": CLICK_1[1],
            "bbox": [45, 37, 30, 16],
            "label": "Open menu",
        },
        {"t": 3.0, "type": "wait", "x": None, "y": None, "bbox": None, "label": "loading"},
        {
            "t": 6.5,
            "type": "click",
            "x": CLICK_2[0],
            "y": CLICK_2[1],
            "bbox": [272, 82, 16, 16],
            "label": "Save",
            "path": [[6.0, 200, 120], [6.2, 240, 105], [6.4, 275, 92]],
        },
        {"t": 7.4, "type": "navigate", "x": None, "y": None, "bbox": None, "label": "/done"},
    ],
}


def _ffexpr_to_python(expr: str):
    """Evaluate an ffmpeg setpts expression of ``T`` with Python semantics."""
    py = expr
    for name in ("lte", "gte", "lt", "if"):
        py = re.sub(rf"\b{name}\(", f"_{name}(", py)
    env = {
        "_if": lambda c, a, b: a if c else b,
        "_lt": lambda a, b: a < b,
        "_lte": lambda a, b: a <= b,
        "_gte": lambda a, b: a >= b,
    }
    code = compile(py, "<expr>", "eval")

    def run(t: float) -> float:
        return float(eval(code, dict(env), {"T": t}))  # noqa: S307 - test-only, built here

    return run


# ---------------------------------------------------------------- pure logic


def test_actions_doc_parses_contract_and_sidecar(tmp_path: Path) -> None:
    doc = actions_from_dict(ACTIONS)
    assert doc.viewport_width == VIEW_W and doc.device_scale_factor == DPR
    assert [e.type for e in doc.events] == ["hover", "click", "wait", "click", "navigate"]
    assert doc.events[1].point() == CLICK_1
    assert doc.events[3].path[0] == (6.0, 200.0, 120.0)
    clip = tmp_path / "rec.mp4"
    clip.write_bytes(b"")
    assert find_actions_sidecar(clip) is None
    (tmp_path / "rec.actions.json").write_text(json.dumps(ACTIONS), encoding="utf-8")
    assert find_actions_sidecar(clip) == tmp_path / "rec.actions.json"


def _camera(events, *, aspect=None, intensity=1.0, n_frames=240, fps=30.0):
    return plan_camera(
        events,
        src_w=640,
        src_h=360,
        scale_x=2.0,
        scale_y=2.0,
        fps=fps,
        n_frames=n_frames,
        aspect=aspect,
        options=CameraOptions(intensity=intensity),
    )


def test_camera_path_is_smooth_bounded_and_zooms_on_clicks() -> None:
    doc = actions_from_dict(ACTIONS)
    cam = _camera(doc.events)
    steps = cam.max_step()
    # Critically damped spring: no frame moves more than ~4% of the frame width
    # or changes zoom by more than ~7%.
    assert steps["center_px"] < 640 * 0.04
    assert steps["log_zoom"] < math.log(1.07)
    for i in range(len(cam)):
        x0, y0, x1, y1 = cam.box(i)
        assert -1e-6 <= x0 and x1 <= 640 + 1e-6
        assert -1e-6 <= y0 and y1 <= 360 + 1e-6
        assert 1.0 <= cam.zoom[i] <= 2.2 + 1e-9
    # Zoomed in around the first click, zoomed into the far-right second click.
    assert cam.zoom[int(1.2 * 30)] > 1.5
    assert cam.zoom[int(6.5 * 30)] > 1.5
    assert cam.cx[int(6.5 * 30)] > 640 * 0.7
    # Zoomed back out after navigate (7.4 s) settles to the full frame.
    assert cam.zoom[-1] < 1.1
    # No jitter: velocity of the center changes sign only a handful of times.
    vel = [cam.cx[i] - cam.cx[i - 1] for i in range(1, len(cam))]
    signs = [v > 0 for v in vel if abs(v) > 0.05]
    flips = sum(1 for a, b in zip(signs, signs[1:]) if a != b)
    assert flips <= 6


def test_camera_merges_bursts_of_typing_into_one_hold() -> None:
    typing = [
        ActionEvent(t=1.0 + k * 0.15, type="type", x=100 + k, y=60, bbox=(80, 50, 60, 20))
        for k in range(12)
    ]
    cam = _camera(typing, n_frames=150)
    held = cam.zoom[int(1.4 * 30) : int(2.6 * 30)]
    assert min(held) > 1.5
    # One target, so the zoom never dips while the keystrokes keep coming.
    assert all(b >= a - 1e-6 for a, b in zip(held, held[1:]))


def test_intensity_off_keeps_full_frame() -> None:
    cam = _camera(actions_from_dict(ACTIONS).events, intensity=0.0)
    assert max(cam.zoom) == pytest.approx(1.0)
    assert cam.box(100) == pytest.approx((0.0, 0.0, 640.0, 360.0))


def test_portrait_camera_follows_off_center_click() -> None:
    cam = _camera(actions_from_dict(ACTIONS).events, aspect=9 / 16)
    i = int(6.5 * 30)
    x0, _, x1, _ = cam.box(i)
    assert x0 <= CLICK_2[0] * 2 <= x1
    # A plain center crop would miss it.
    center_w = 360 * 9 / 16
    assert not (320 - center_w / 2 <= CLICK_2[0] * 2 <= 320 + center_w / 2)


def test_cursor_track_hits_click_and_animates() -> None:
    doc = actions_from_dict(ACTIONS)
    track = plan_cursor(
        doc.events, scale_x=2, scale_y=2, src_w=640, src_h=360, fps=30, n_frames=240
    )
    assert track is not None
    i = int(round(1.2 * 30))
    assert track.x[i] == pytest.approx(CLICK_1[0] * 2, abs=1.0)
    assert track.y[i] == pytest.approx(CLICK_1[1] * 2, abs=1.0)
    assert track.scale[i] < 0.9  # pressed
    assert track.ripples[i + 3]  # ripple running after the click
    assert track.scale[i + 20] == 1.0 and not track.ripples[i + 20]
    # Smooth: no teleport between frames.
    jumps = [
        math.hypot(track.x[k] - track.x[k - 1], track.y[k] - track.y[k - 1]) for k in range(1, 240)
    ]
    assert max(jumps) < 60


def test_dead_segments_skip_action_windows_and_remap_events() -> None:
    doc = actions_from_dict(ACTIONS)
    remap = plan_dead_segments([(2.0, 6.0)], doc.events, threshold=1.2, speed=6.0)
    assert len(remap.segments) == 1
    seg = remap.segments[0]
    assert seg.start == pytest.approx(2.3) and seg.end == pytest.approx(5.7)
    # Before the span untouched, after it shifted by exactly the loss.
    assert remap.map(1.2) == pytest.approx(1.2)
    assert remap.map(6.5) == pytest.approx(6.5 - seg.loss)
    assert seg.loss == pytest.approx((1 - 1 / 6) * (3.4 - seg.ramp))
    # Monotonic and continuous.
    ts = [k / 100 for k in range(0, 801)]
    mapped = [remap.map(t) for t in ts]
    assert all(b >= a for a, b in zip(mapped, mapped[1:]))
    assert max(b - a for a, b in zip(mapped, mapped[1:])) <= 0.0100001
    out = remap_actions(doc, remap, DURATION)
    assert out.events[3].t == pytest.approx(6.5 - seg.loss)
    assert out.events[3].path[0][0] == pytest.approx(remap.map(6.0))
    assert out.duration == pytest.approx(DURATION - seg.loss)
    # The ffmpeg setpts expression is the same function.
    run = _ffexpr_to_python(remap.setpts_expr())
    for t in ts:
        assert run(t) == pytest.approx(remap.map(t), abs=1e-5)


def test_cut_mode_and_short_freezes() -> None:
    events = [ActionEvent(t=0.5, type="click", x=10, y=10)]
    cut = plan_dead_segments([(1.0, 4.0), (5.0, 5.8)], events, mode="cut")
    assert len(cut.segments) == 1  # 0.8 s freeze is below the threshold
    assert math.isinf(cut.segments[0].speed)
    # Guard [0.1, 1.3] around the click, then 0.3 s margins on both sides.
    assert cut.removed == pytest.approx((4.0 - 0.3) - (1.3 + 0.3))
    assert cut.map(4.5) == pytest.approx(4.5 - cut.removed)


def test_remap_srt_shifts_cues() -> None:
    remap = TimeRemap((DeadSegment(2.0, 4.0, math.inf, 0.0),))
    srt = "1\n00:00:01,000 --> 00:00:01,500\nHello\n\n2\n00:00:05,000 --> 00:00:06,000\nWorld\n"
    out = remap_srt(srt, remap)
    assert "00:00:01,000 --> 00:00:01,500" in out
    assert "00:00:03,000 --> 00:00:04,000" in out


# ---------------------------------------------------------------- ffmpeg


@pytest.fixture(scope="module")
def recording(tmp_path_factory: pytest.TempPathFactory) -> Path:
    if not (FFMPEG and FFPROBE):
        pytest.skip("ffmpeg/ffprobe missing")
    root = tmp_path_factory.mktemp("footage")
    clip = root / "rec.mp4"
    w, h = VIEW_W * DPR, VIEW_H * DPR
    gx, gy = int(CLICK_2[0] * DPR) - 16, int(CLICK_2[1] * DPR) - 16
    graph = (
        f"color=c=0x808080:s={w}x{h}:r={FPS}:d={DURATION}[bg];"
        f"color=c=red:s=40x40:r={FPS}:d={DURATION}[box];"
        "[bg][box]overlay=x='if(between(t,2,6),150,100+25*t)':y=220:shortest=1,"
        f"drawbox=x={gx}:y={gy}:w=32:h=32:color=0x00ff00:t=fill,"
        "drawbox=x=8:y=8:w=40:h=40:color=0x0000ff:t=fill:enable='gte(t,6)'"
    )
    subprocess.run(
        [
            FFMPEG,
            "-nostdin",
            "-y",
            "-loglevel",
            "error",
            "-f",
            "lavfi",
            "-i",
            graph,
            "-c:v",
            "libx264",
            "-preset",
            "ultrafast",
            "-pix_fmt",
            "yuv420p",
            "-r",
            str(FPS),
            str(clip),
        ],
        check=True,
        timeout=120,
    )
    (root / "rec.actions.json").write_text(json.dumps(ACTIONS), encoding="utf-8")
    return clip


def _probe(path: Path) -> dict:
    out = subprocess.run(
        [
            FFPROBE,
            "-v",
            "error",
            "-count_frames",
            "-show_entries",
            "stream=width,height,r_frame_rate,nb_read_frames:format=duration",
            "-of",
            "json",
            str(path),
        ],
        capture_output=True,
        text=True,
        check=True,
        timeout=60,
    )
    data = json.loads(out.stdout)
    stream = data["streams"][0]
    return {
        "width": int(stream["width"]),
        "height": int(stream["height"]),
        "fps": stream["r_frame_rate"],
        "frames": int(stream["nb_read_frames"]),
        "duration": float(data["format"]["duration"]),
    }


def _frame_rgb(path: Path, index: int, size: tuple[int, int]) -> bytes:
    out = subprocess.run(
        [
            FFMPEG,
            "-nostdin",
            "-loglevel",
            "error",
            "-i",
            str(path),
            "-vf",
            f"select=eq(n\\,{index})",
            "-frames:v",
            "1",
            "-f",
            "rawvideo",
            "-pix_fmt",
            "rgb24",
            "-",
        ],
        capture_output=True,
        check=True,
        timeout=60,
    )
    assert len(out.stdout) == size[0] * size[1] * 3
    return out.stdout


def _px(buf: bytes, width: int, x: int, y: int) -> tuple[int, int, int]:
    o = (y * width + x) * 3
    return buf[o], buf[o + 1], buf[o + 2]


def _fast(**kw) -> FootageOptions:
    return FootageOptions(preset="ultrafast", crf=18, **kw)


@needs_ffmpeg
def test_polish_output_duration_resolution_fps_and_remap(recording: Path, tmp_path: Path) -> None:
    out = tmp_path / "polished.mp4"
    result = polish_footage(recording, None, out, options=_fast(), width=640, height=360)
    assert result["ok"] and out.is_file()
    info = _probe(out)
    removed = result["dead_time"]["removed_seconds"]
    assert removed > 2.0  # the 4 s freeze is sped up
    expected = DURATION - removed
    assert info["width"] == 640 and info["height"] == 360
    assert info["fps"] == "30/1"
    assert info["frames"] == result["frames"]
    assert abs(result["frames"] - expected * FPS) <= 1
    assert info["duration"] == pytest.approx(expected, abs=1.5 / FPS)
    payload = json.loads(Path(result["actions"]).read_text(encoding="utf-8"))
    assert payload["polish"]["dead_time"]["segments"]
    save = next(e for e in payload["events"] if e["label"] == "Save")
    assert save["t"] == pytest.approx(6.5 - removed, abs=1e-3)
    assert payload["duration"] == pytest.approx(info["frames"] / FPS, abs=1e-3)


@needs_ffmpeg
def test_trimmed_content_lands_on_remapped_time(recording: Path, tmp_path: Path) -> None:
    plan = prepare_footage(recording, None, _fast(intensity=0.0, cursor="none"))
    out = tmp_path / "flat.mp4"
    render_footage(plan, out, width=640, height=360)
    expected = plan.remap.map(6.0) * FPS  # the blue badge appears at source 6.0 s

    def blue(index: int) -> bool:
        r, g, b = _px(_frame_rgb(out, index, (640, 360)), 640, 28, 28)
        return b > 180 and r < 80 and g < 80

    first = int(math.ceil(expected - 1e-6))
    assert blue(first + 1)
    assert not blue(first - 2)


@needs_ffmpeg
def test_cursor_drawn_at_click_time(recording: Path, tmp_path: Path) -> None:
    plan = prepare_footage(recording, None, _fast(intensity=0.0, cursor="light", trim=False))
    with_cursor = render_footage(plan, tmp_path / "c.mp4", width=640, height=360)
    click = next(e for e in with_cursor["event_frames"] if e["type"] == "click")
    frame, x, y = click["frame"], int(click["x"]), int(click["y"])
    # Inside the arrow body, just below-right of the tip (pressed scale 0.84).
    sample = (x + 6, y + 20)
    lit = _frame_rgb(tmp_path / "c.mp4", frame, (640, 360))
    assert min(_px(lit, 640, *sample)) > 200

    plain_plan = prepare_footage(recording, None, _fast(intensity=0.0, cursor="none", trim=False))
    render_footage(plain_plan, tmp_path / "n.mp4", width=640, height=360)
    plain = _frame_rgb(tmp_path / "n.mp4", frame, (640, 360))
    assert max(_px(plain, 640, *sample)) < 160


@needs_ffmpeg
def test_vertical_smart_crop_follows_off_center_click(recording: Path, tmp_path: Path) -> None:
    plan = prepare_footage(recording, None, _fast(cursor="none"))
    out = tmp_path / "vertical.mp4"
    result = render_footage(plan, out, width=180, height=320)
    info = _probe(out)
    assert (info["width"], info["height"]) == (180, 320)
    save = [e for e in result["event_frames"] if e["type"] == "click"][-1]
    assert 0 <= save["x"] <= 180
    buf = _frame_rgb(out, save["frame"], (180, 320))
    green = sum(
        1 for k in range(0, len(buf), 3) if buf[k + 1] > 200 and buf[k] < 90 and buf[k + 2] < 90
    )
    assert green > 50  # the far-right button is in the 9:16 frame


@needs_ffmpeg
def test_framed_layout_has_branded_background(recording: Path, tmp_path: Path) -> None:
    plan = prepare_footage(
        recording,
        None,
        _fast(cursor="none", layout="framed", title="Ship faster", captions="actions"),
    )
    out = tmp_path / "framed.mp4"
    result = render_footage(plan, out, width=360, height=640)
    assert result["layout"] == "framed"
    info = _probe(out)
    assert (info["width"], info["height"]) == (360, 640)
    buf = _frame_rgb(out, 10, (360, 640))
    r, g, b = _px(buf, 360, 180, 4)
    assert b > r and b >= 20  # navy brand background, not a black bar
    r, g, b = _px(buf, 360, 180, 300)
    assert abs(r - 128) < 20 and abs(g - 128) < 20  # the footage sits in the middle


@needs_ffmpeg
def test_package_uses_polish_when_actions_exist(recording: Path, tmp_path: Path) -> None:
    from navin.montage.demo import package_social, render_package_result

    root = tmp_path / "ws"
    demo = root / "demo.mp4"
    demo.parent.mkdir(parents=True)
    shutil.copy2(recording, demo)
    (root / "demo.actions.json").write_text(json.dumps(ACTIONS), encoding="utf-8")
    result = package_social(
        root,
        "demo.mp4",
        profiles="instagram_feed",
        footage={"preset": "ultrafast"},
    )
    assert result["ok"] and result.get("polished") == ["instagram_feed"]
    assert (root / result["actions"]).is_file()
    info = _probe(root / result["exports"]["instagram_feed"])
    assert (info["width"], info["height"]) == (1080, 1080)
    assert "polished" in render_package_result(result)


@needs_ffmpeg
def test_montage_tool_footage_polish_action(recording: Path, tmp_path: Path) -> None:
    from navin.agent.tools.montage import MontageTool

    root = tmp_path / "ws"
    root.mkdir()
    shutil.copy2(recording, root / "rec.mp4")
    shutil.copy2(recording.with_name("rec.actions.json"), root / "rec.actions.json")
    tool = MontageTool.__new__(MontageTool)
    tool.workspace = root

    async def run() -> str:
        with (
            patch.object(tool, "_root_or_error", return_value=(root, "")),
            patch.object(
                tool, "_resolve_media_input", side_effect=lambda r, v: str((r / v).resolve())
            ),
        ):
            return await tool.execute(
                action="footage_polish",
                path="rec.mp4",
                width=320,
                height=180,
                intensity="subtle",
                cursor="dark",
                footage_options=json.dumps({"preset": "ultrafast"}),
            )

    payload = json.loads(asyncio.run(run()))
    assert payload["ok"], payload
    assert payload["output"] == "marketing/montage/exports/rec-polished.mp4"
    assert (root / payload["actions"]).is_file()
    assert payload["cursor"] == "dark"
    assert (payload["width"], payload["height"]) == (320, 180)
