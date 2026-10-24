from __future__ import annotations

import sys
import types
from pathlib import Path
from types import SimpleNamespace

import pytest

from navin.visual import compare, figma, pipeline, video_steps


def _img(path, color, box=None):
    from PIL import Image

    im = Image.new("RGB", (80, 80), color)
    if box:
        im.paste((0, 0, 0), box)
    im.save(path)
    return path


def _fake_playwright(monkeypatch, calls):
    """Install a stand-in ``playwright.sync_api`` that records each call."""

    class Page:
        def set_content(self, html):
            calls.append(("html", html))

        def screenshot(self, path, full_page):
            Path(path).write_bytes(b"png")
            calls.append(("shot", full_page))

    class Browser:
        def new_page(self, viewport):
            calls.append(("viewport", viewport))
            return Page()

        def close(self):
            calls.append(("closed", None))

    class Playwright:
        chromium = SimpleNamespace(launch=lambda: Browser())

        def __enter__(self):
            return self

        def __exit__(self, *exc):
            return False

    sync_api = types.ModuleType("playwright.sync_api")
    sync_api.sync_playwright = lambda: Playwright()
    monkeypatch.setitem(sys.modules, "playwright", types.ModuleType("playwright"))
    monkeypatch.setitem(sys.modules, "playwright.sync_api", sync_api)
    monkeypatch.setattr(compare, "have_playwright", lambda: True)


def test_identical(tmp_path):
    a = _img(tmp_path / "a.png", (255, 255, 255))
    b = _img(tmp_path / "b.png", (255, 255, 255))
    r = compare.compare_images(a, b)
    assert r["score"] == 1.0 and r["zones"] == []


def test_diff_zone_and_outputs(tmp_path):
    a = _img(tmp_path / "a.png", (255, 255, 255))
    b = _img(tmp_path / "b.png", (255, 255, 255), (0, 0, 20, 20))
    r = compare.compare_images(a, b, tmp_path / "d.png", tmp_path / "r.json")
    assert r["score"] < 1.0 and r["zones"]
    assert (tmp_path / "d.png").exists() and (tmp_path / "r.json").exists()


def test_pipeline_iterates(tmp_path):
    ref = _img(tmp_path / "ref.png", (255, 255, 255))
    bad = (0, 0, 0)

    def render(html, out):
        return _img(out, (255, 255, 255) if html == "good" else bad)

    def gen(_ref, i, _last):
        return "good" if i == 2 else "bad"

    res = pipeline.run_pipeline(ref, gen, tmp_path / "o", 0.99, 3, render)
    assert res["reached"] and res["iterations"] == 2


def test_render_html_writes_screenshot(tmp_path, monkeypatch):
    calls: list[tuple] = []
    _fake_playwright(monkeypatch, calls)
    out = compare.render_html("<h1>x</h1>", tmp_path / "sub" / "x.png", 200, 100)
    assert out.exists()
    assert ("viewport", {"width": 200, "height": 100}) in calls
    assert ("html", "<h1>x</h1>") in calls
    assert ("shot", False) in calls
    assert calls[-1] == ("closed", None)


def test_render_html_without_playwright(tmp_path, monkeypatch):
    monkeypatch.setattr(compare, "have_playwright", lambda: False)
    with pytest.raises(RuntimeError, match="playwright"):
        compare.render_html("<p></p>", tmp_path / "x.png")
    assert not (tmp_path / "x.png").exists()


# Figma ---------------------------------------------------------------------


def test_parse_figma_urls():
    assert figma.parse_figma_url("https://www.figma.com/file/AbC123/Name?node-id=1-2") == ("AbC123", "1:2")
    assert figma.parse_figma_url("https://figma.com/design/XyZ9/T?node-id=10%3A20&t=x") == ("XyZ9", "10:20")
    assert figma.parse_figma_url("KEY42", "3-4") == ("KEY42", "3:4")
    with pytest.raises(figma.FigmaError, match="Unrecognized"):
        figma.parse_figma_url("https://figma.com/community/abc")


class _Resp:
    def __init__(self, status, data=None, content=b""):
        self.status_code, self._data, self.content = status, data, content

    def json(self):
        return self._data


def test_fetch_figma_frame_mocked(tmp_path):
    calls = []

    def http(method, url, headers, params):
        calls.append((method, url, headers, params))
        if url.startswith(figma.FIGMA_API):
            return _Resp(200, {"err": None, "images": {"1:2": "https://cdn.example/img.png"}})
        return _Resp(200, content=b"PNGDATA")

    out = tmp_path / "ref" / "f.png"
    res = figma.fetch_figma_frame("https://www.figma.com/design/K1/x?node-id=1-2", None, out, token="tok", http=http)
    assert out.read_bytes() == b"PNGDATA" and res["node_id"] == "1:2"
    method, url, headers, params = calls[0]
    assert (method, url) == ("GET", "https://api.figma.com/v1/images/K1")
    assert headers == {"X-Figma-Token": "tok"}
    assert params == {"ids": "1:2", "format": "png"}
    assert calls[1][1] == "https://cdn.example/img.png"


def test_fetch_figma_errors(tmp_path, monkeypatch):
    monkeypatch.delenv("FIGMA_TOKEN", raising=False)
    with pytest.raises(figma.FigmaError, match="token missing"):
        figma.fetch_figma_frame("K1", "1:2", tmp_path / "f.png", http=lambda *a, **k: None)
    with pytest.raises(figma.FigmaError, match="not found"):
        figma.fetch_figma_frame("K1", "9:9", tmp_path / "f.png", token="t",
                                http=lambda *a, **k: _Resp(200, {"images": {"9:9": None}}))
    with pytest.raises(figma.FigmaError, match="HTTP 403"):
        figma.fetch_figma_frame("K1", "9:9", tmp_path / "f.png", token="t",
                                http=lambda *a, **k: _Resp(403, {}))
    assert not (tmp_path / "f.png").exists()


# Video steps ---------------------------------------------------------------


def test_extract_keyframes_mocked_runner(tmp_path):
    video = tmp_path / "v.mp4"
    video.write_bytes(b"x")
    cmds = []

    def runner(cmd):
        cmds.append(cmd)
        out = Path(cmd[-1])
        for i in range(3):
            _img(out.parent / (out.name.replace("%04d", f"{i + 1:04d}")), (i * 80, 0, 0))
        return SimpleNamespace(returncode=0, stdout="", stderr="pts_time:0.5 x pts_time:1.5 y pts_time:3")

    frames = video_steps.extract_keyframes(video, tmp_path / "f", 5, 0.4, runner=runner)
    assert len(cmds) == 1 and cmds[0][0] == "ffmpeg"
    assert "select='gt(scene,0.4)',showinfo" in cmds[0]
    assert [f["timestamp"] for f in frames] == [0.5, 1.5, 3.0]


def test_extract_keyframes_falls_back_to_sampling(tmp_path):
    video = tmp_path / "v.mp4"
    video.write_bytes(b"x")
    cmds = []

    def runner(cmd):
        cmds.append(cmd)
        if cmd[0] == "ffprobe":
            return SimpleNamespace(returncode=0, stdout="4.0\n", stderr="")
        out = Path(cmd[-1])
        n = 1 if out.name.startswith("scene") else 4
        for i in range(n):
            _img(out.parent / out.name.replace("%04d", f"{i + 1:04d}"), (i * 60, 0, 0))
        return SimpleNamespace(returncode=0, stdout="", stderr="")

    frames = video_steps.extract_keyframes(video, tmp_path / "f", 4, 0.3, runner=runner)
    assert [c[0] for c in cmds] == ["ffmpeg", "ffprobe", "ffmpeg"]
    assert "fps=1/1.000000,showinfo" in cmds[2]
    assert len(frames) == 4 and all("sample_" in f["frame_path"] for f in frames)


def test_extract_keyframes_failure_is_raised(tmp_path):
    video = tmp_path / "v.mp4"
    video.write_bytes(b"x")
    with pytest.raises(RuntimeError, match="ffmpeg failed"):
        video_steps.extract_keyframes(
            video, tmp_path / "f", runner=lambda c: SimpleNamespace(returncode=1, stdout="", stderr="boom")
        )


def test_steps_from_frames_dedup(tmp_path):
    a = _img(tmp_path / "a.png", (255, 255, 255))
    b = _img(tmp_path / "b.png", (255, 255, 255))
    c = _img(tmp_path / "c.png", (0, 0, 0))
    frames = [{"frame_path": str(p), "timestamp": float(i)} for i, p in enumerate([a, b, c])]
    steps = video_steps.steps_from_frames(frames)
    assert steps == [
        {"index": 0, "timestamp": 0.0, "frame_path": str(a)},
        {"index": 1, "timestamp": 2.0, "frame_path": str(c)},
    ]


def test_extract_keyframes_real_ffmpeg(tmp_path):
    import shutil
    import subprocess

    if not shutil.which("ffmpeg"):
        # ci.yml installs ffmpeg and runs this; the release job has no ffmpeg.
        pytest.skip("ffmpeg is not installed on this runner")
    video = tmp_path / "v.mp4"
    subprocess.run(
        ["ffmpeg", "-hide_banner", "-y", "-f", "lavfi", "-i", "color=c=red:s=64x64:d=1",
         "-f", "lavfi", "-i", "color=c=blue:s=64x64:d=1",
         "-filter_complex", "[0][1]concat=n=2:v=1", "-pix_fmt", "yuv420p", str(video)],
        check=True, capture_output=True,
    )
    frames = video_steps.extract_keyframes(video, tmp_path / "f", 4, 0.3)
    steps = video_steps.steps_from_frames(frames)
    assert frames and 1 <= len(steps) <= len(frames)
    assert len(steps) == 2


# Tool action compare -------------------------------------------------------


def test_visual_qa_tool_compare_action(tmp_path):
    import asyncio
    import json

    from navin.agent.tools.visual_qa import VisualQATool, VisualQAToolConfig

    _img(tmp_path / "ref.png", (255, 255, 255))
    _img(tmp_path / "got.png", (255, 255, 255), (0, 0, 40, 40))
    tool = VisualQATool(workspace=tmp_path, config=VisualQAToolConfig())
    out = asyncio.run(tool.execute(action="compare", reference="ref.png", candidate="got.png", threshold=0.9))
    data = json.loads(out)
    assert data["score"] == 0.75 and data["passed"] is False
    assert Path(data["diff_path"]).exists() and Path(data["report_path"]).exists()
    assert Path(data["diff_path"]).parent == (tmp_path / "visual").resolve()
    err = asyncio.run(tool.execute(action="compare", reference="/etc/passwd", candidate="got.png"))
    assert "failed" in str(err)
