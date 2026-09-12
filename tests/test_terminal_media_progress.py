"""Live measurements and native visual content must reach terminal agents."""

import asyncio
import io
import subprocess
import sys

import pytest
from rich.console import Console
from textual.widgets import Static

from navin.agent.tool_output import bind_tool_call_meta, bind_tool_output_emitter
from navin.agent.tools.filesystem import ReadFileTool
from navin.agent.tools.quality import TestRunTool as RunTool
from navin.cli.activity import ActivityPrinter
from navin.quality.testing import _run_streaming
from navin.tui.widgets import ToolCall
from navin.utils import video_frames
from tests.test_tui_activity import ActivityHost


def test_real_test_suite_streams_measurements_before_completion(tmp_path):
    (tmp_path / "tests").mkdir()
    (tmp_path / "tests/test_sample.py").write_text(
        "import time\nimport pytest\n"
        "@pytest.mark.parametrize('case', range(10))\n"
        "def test_case(case):\n    time.sleep(0.16)\n    assert case >= 0\n"
    )

    async def run():
        events = []

        async def collect(event):
            events.append(event)

        bind_tool_output_emitter(collect)
        bind_tool_call_meta("tests", "test_run", {"runner": "pytest"})
        try:
            result = await RunTool(workspace=tmp_path).execute(action="run", runner="pytest")
        finally:
            bind_tool_output_emitter(None)
        percentages = [event["percent"] for event in events if "percent" in event]
        assert 50 in percentages and 70 in percentages
        assert percentages[-1] == 100
        assert "10 passed" in result
        terminal = io.StringIO()
        printer = ActivityPrinter(Console(file=terminal, width=120))
        printer.consume(tool_events=events)
        assert "50%" in terminal.getvalue() and "70%" in terminal.getvalue()
        # Repeated cumulative snapshots must not repeat completed test rows.
        assert terminal.getvalue().count("test_case[0] PASSED") == 1

    asyncio.run(run())


@pytest.mark.parametrize("theme", ["navin", "navin-light"])
def test_tui_updates_same_row_and_keeps_last_measurement_on_failure(theme):
    async def run():
        app = ActivityHost(theme)
        async with app.run_test(size=(90, 28)):
            await app.block.tool_event("run", "exec", "start", {"command": "pytest"}, None, None, None)
            for percent in (50, 70):
                await app.block.tool_event(
                    "run", "exec", "output", {}, None, None, f"case passed [{percent}%]\n",
                    percent=percent, output_mode="snapshot",
                )
                row = app.block.query_one(ToolCall)
                assert f"{percent}%" in str(row.query_one(".tool-head", Static).content)
            assert len(app.block.query(ToolCall)) == 1
            assert row.output_lines == ["case passed [70%]"]
            await app.block.tool_event("run", "exec", "error", {}, None, "Stopped", None)
            assert "70%" in row._head_text() and "100%" not in row._head_text()

    asyncio.run(run())


def test_streaming_runner_enforces_timeout_and_preserves_output():
    chunks = []
    with pytest.raises(subprocess.TimeoutExpired):
        _run_streaming(
            [sys.executable, "-u", "-c", "import time; print('50%', flush=True); time.sleep(10)"],
            timeout=0.4, on_output=chunks.append,
        )
    assert "50%" in "".join(chunks)


def test_read_video_returns_timestamped_native_images(tmp_path, monkeypatch):
    from PIL import Image

    video = tmp_path / "clip.mp4"
    video.write_bytes(b"video fixture")
    frame = tmp_path / "frame.png"
    Image.new("RGB", (32, 32), "red").save(frame)
    monkeypatch.setattr(video_frames, "extract_video_frames", lambda path: video_frames.VideoFrames(
        source=str(path), paths=[str(frame)], timestamps=[3.5], duration=7,
    ))
    tool = ReadFileTool(workspace=tmp_path, allowed_dir=tmp_path)
    blocks = asyncio.run(tool.execute(path=str(video)))
    assert isinstance(blocks, list)
    images = [block for block in blocks if block["type"] == "image_url"]
    assert len(images) == 1
    assert images[0]["image_url"]["url"].startswith("data:image/png;base64,")
    text = " ".join(block["text"] for block in blocks if block["type"] == "text")
    assert "3.5s" in text and "audio have not been read" in text


def test_read_video_reports_unavailable_decoder(tmp_path, monkeypatch):
    video = tmp_path / "clip.mp4"
    video.write_bytes(b"video fixture")
    monkeypatch.setattr(video_frames, "_find_ffmpeg", lambda: None)
    result = asyncio.run(ReadFileTool(workspace=tmp_path).execute(path=str(video)))
    assert result.is_error and "ffmpeg" in result
