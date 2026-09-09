"""Montage editing loop: probe, streamed progress, cancellation, background jobs,
timeline actions shared between the agent and the studio UI."""

from __future__ import annotations

import asyncio
import json
import sys
from pathlib import Path

import pytest

from navin.montage import jobs as jobs_module
from navin.montage.assemble import ProgressParser, RenderProgress, spec_from_paths
from navin.montage.ffmpeg_runner import run_process
from navin.montage.jobs import (
    JobContext,
    cancel_job,
    create_job,
    get_job,
    job_context,
    job_summary,
    run_job,
    start_job,
    wait_for_job,
)
from navin.montage.probe import (
    MediaInfo,
    ProbeError,
    clear_probe_cache,
    media_info_from_ffmpeg_header,
    media_info_from_ffprobe,
    media_kind,
    probe_media,
)
from navin.montage.timeline import (
    get_timeline,
    import_media,
    put_timeline,
    timeline_from_spec,
)

FFPROBE_VIDEO = {
    "streams": [
        {
            "codec_type": "video",
            "codec_name": "h264",
            "width": 1920,
            "height": 1080,
            "avg_frame_rate": "30000/1001",
            "r_frame_rate": "30000/1001",
        },
        {"codec_type": "audio", "codec_name": "aac", "duration": "12.5"},
    ],
    "format": {"format_name": "mov,mp4,m4a,3gp,3g2,mj2", "duration": "12.480"},
}

FFMPEG_HEADER = """Input #0, mov,mp4,m4a,3gp,3g2,mj2, from 'demo.mp4':
  Duration: 00:00:02.00, start: 0.000000, bitrate: 89 kb/s
  Stream #0:0[0x1](und): Video: h264 (High) (avc1 / 0x31637661), yuv420p(progressive), 320x240 [SAR 1:1 DAR 4:3], 6 kb/s, 25 fps, 25 tbr, 12800 tbn (default)
  Stream #0:1[0x2](und): Audio: aac (LC) (mp4a / 0x6134706D), 44100 Hz, mono, fltp, 69 kb/s (default)
At least one output file must be specified
"""


class TestProbeMapping:
    def test_ffprobe_document_maps_to_media_info(self):
        info = media_info_from_ffprobe("clip.mp4", FFPROBE_VIDEO, size_bytes=10)
        assert info.kind == "video"
        assert info.duration == 12.48
        assert (info.width, info.height) == (1920, 1080)
        assert info.fps == pytest.approx(29.97, abs=0.001)
        assert info.has_video and info.has_audio
        assert info.video_codec == "h264" and info.audio_codec == "aac"
        assert info.source == "ffprobe"

    def test_a_still_has_no_duration_even_if_ffprobe_reports_one(self):
        payload = {
            "streams": [{"codec_type": "video", "codec_name": "png", "width": 64, "height": 48}],
            "format": {"format_name": "png_pipe", "duration": "0.04"},
        }
        info = media_info_from_ffprobe("frame.png", payload, size_bytes=1)
        assert info.kind == "image"
        assert info.duration is None and info.fps is None
        assert (info.width, info.height) == (64, 48)
        assert info.has_video is False

    def test_the_ffmpeg_header_fallback_reads_size_fps_and_audio(self):
        info = media_info_from_ffmpeg_header("demo.mp4", FFMPEG_HEADER, size_bytes=5)
        assert info.duration == 2.0
        assert (info.width, info.height) == (320, 240)
        assert info.fps == 25.0
        assert info.has_audio is True
        assert info.video_codec == "h264" and info.audio_codec == "aac"
        assert info.source == "ffmpeg"

    def test_media_kind_covers_gif_and_audio(self):
        assert media_kind("a.GIF") == "video"
        assert media_kind("a.png") == "image"
        assert media_kind("a.mp3") == "audio"
        assert media_kind("a.txt") == "file"

    def test_bad_ffprobe_payload_is_a_probe_error(self):
        with pytest.raises(ProbeError):
            media_info_from_ffprobe("x.mp4", "nope", size_bytes=0)


class TestProbeMedia:
    def test_missing_file_raises(self, tmp_path):
        with pytest.raises(ProbeError):
            asyncio.run(probe_media(tmp_path / "missing.mp4"))

    def test_results_are_cached_until_the_file_changes(self, tmp_path, monkeypatch):
        clear_probe_cache()
        target = tmp_path / "clip.mp4"
        target.write_bytes(b"one")
        calls = []

        async def fake_run(argv, **kwargs):
            calls.append(list(argv))
            from navin.montage.ffmpeg_runner import ProcessResult

            return ProcessResult(
                ok=True,
                argv=tuple(argv),
                command="ffprobe",
                exit_code=0,
                stdout=json.dumps(FFPROBE_VIDEO),
                stderr="",
                raw_stderr="",
                duration_s=0.1,
            )

        monkeypatch.setattr("navin.montage.probe.run_process", fake_run)
        monkeypatch.setattr("navin.montage.detect.find_ffmpeg", lambda: "/opt/ffmpeg")
        monkeypatch.setattr("navin.montage.probe.find_ffprobe", lambda _b=None: "/opt/ffprobe")
        first = asyncio.run(probe_media(target))
        second = asyncio.run(probe_media(target))
        assert first == second
        assert first["duration"] == 12.48
        assert len(calls) == 1
        target.write_bytes(b"two-bytes-longer")
        asyncio.run(probe_media(target))
        assert len(calls) == 2

    def test_no_toolchain_still_describes_the_file(self, tmp_path, monkeypatch):
        clear_probe_cache()
        target = tmp_path / "clip.mp4"
        target.write_bytes(b"x")
        monkeypatch.setattr("navin.montage.detect.find_ffmpeg", lambda: None)
        info = asyncio.run(probe_media(target, use_cache=False))
        assert info["source"] == "none"
        assert info["kind"] == "video"
        assert info["duration"] is None


class TestProgressParser:
    def test_blocks_become_reports_with_fractions(self):
        parser = ProgressParser(total_duration=10.0)
        assert parser.feed("frame=12") is None
        assert parser.feed("fps=24.0") is None
        assert parser.feed("out_time_us=2500000") is None
        assert parser.feed("speed=1.5x") is None
        report = parser.feed("progress=continue")
        assert isinstance(report, RenderProgress)
        assert report.out_time_s == 2.5
        assert report.fraction == 0.25
        assert report.frame == 12 and report.fps == 24.0 and report.speed == 1.5
        assert report.done is False
        parser.feed("out_time_us=10000000")
        final = parser.feed("progress=end")
        assert final is not None and final.done is True and final.fraction == 1.0

    def test_unknown_total_keeps_time_but_no_fraction(self):
        parser = ProgressParser(total_duration=None)
        parser.feed("out_time_ms=1500000")
        report = parser.feed("progress=continue")
        assert report is not None
        assert report.out_time_s == 1.5
        assert report.fraction is None
        assert report.to_dict()["out_time_s"] == 1.5


class TestRunProcessStreaming:
    def test_stdout_lines_are_streamed_while_the_process_runs(self):
        lines: list[str] = []
        script = "import sys\nfor i in range(3):\n    print('line', i, flush=True)\n"
        result = asyncio.run(
            run_process(
                [sys.executable, "-c", script],
                timeout_s=30,
                on_stdout_line=lines.append,
            )
        )
        assert result.ok
        assert lines == ["line 0", "line 1", "line 2"]
        assert result.cancelled is False

    def test_a_cancel_event_kills_the_process(self):
        script = "import time,sys\nprint('started', flush=True)\ntime.sleep(30)\n"

        async def scenario():
            cancel = asyncio.Event()
            seen: list[str] = []

            def on_line(line: str) -> None:
                seen.append(line)
                cancel.set()

            return await run_process(
                [sys.executable, "-c", script],
                timeout_s=60,
                on_stdout_line=on_line,
                cancel=cancel,
            ), seen

        result, seen = asyncio.run(asyncio.wait_for(scenario(), timeout=20))
        assert seen == ["started"]
        assert result.cancelled is True
        assert result.ok is False
        assert result.error is not None and result.error.kind == "cancelled"

    def test_a_pre_set_cancel_event_stops_before_spawning_work(self):
        script = "import time\ntime.sleep(30)\n"

        async def scenario():
            cancel = asyncio.Event()
            cancel.set()
            return await run_process([sys.executable, "-c", script], timeout_s=60, cancel=cancel)

        result = asyncio.run(asyncio.wait_for(scenario(), timeout=20))
        assert result.cancelled is True


class TestBackgroundJobs:
    def test_start_job_runs_in_the_background_and_persists_progress(self, tmp_path):
        async def scenario():
            job = create_job(tmp_path, "assemble", ["render"], payload={})
            gate = asyncio.Event()
            reports: list[str] = []

            async def render(payload, manifest):
                context = job_context(manifest["id"])
                assert context is not None
                context.report_progress({"fraction": 0.25, "out_time_s": 1.0})
                await gate.wait()
                context.report_progress({"fraction": 1.0, "out_time_s": 4.0, "done": True})
                return {"ok": True, "output": "final.mp4"}

            started = start_job(
                tmp_path,
                job["id"],
                {"render": render},
                notify=lambda manifest: reports.append(manifest["status"]),
            )
            assert started["status"] == "pending"
            await asyncio.sleep(0.05)
            live = get_job(tmp_path, job["id"])
            assert live["status"] == "running"
            assert live["progress"]["fraction"] == 0.25
            gate.set()
            final = await wait_for_job(job["id"], timeout_s=5)
            assert final is not None and final["status"] == "completed"
            assert final["progress"]["fraction"] == 1.0
            assert "running" in reports and reports[-1] == "completed"
            assert job_context(job["id"]) is None
            summary = job_summary(final)
            assert summary["output"] == "final.mp4" and summary["status"] == "completed"

        asyncio.run(asyncio.wait_for(scenario(), timeout=10))

    def test_cancel_job_stops_a_running_step_and_marks_the_manifest(self, tmp_path):
        async def scenario():
            job = create_job(tmp_path, "timeline-render", ["render"], payload={"timeline": "demo"})

            async def render(payload, manifest):
                context = job_context(manifest["id"])
                assert context is not None
                await context.cancel.wait()
                return {"ok": False, "cancelled": True, "error": "render cancelled"}

            start_job(tmp_path, job["id"], {"render": render})
            await asyncio.sleep(0.05)
            asked = cancel_job(tmp_path, job["id"])
            assert asked["cancel_requested"] is True
            final = await wait_for_job(job["id"], timeout_s=5)
            assert final is not None
            assert final["status"] == "cancelled"
            assert final["steps"][0]["status"] == "cancelled"
            assert final["error"] is None
            on_disk = get_job(tmp_path, job["id"])
            assert on_disk["status"] == "cancelled"

        asyncio.run(asyncio.wait_for(scenario(), timeout=10))

    def test_cancel_job_flips_a_stale_manifest_without_a_runner(self, tmp_path):
        job = create_job(tmp_path, "assemble", ["render"], payload={})
        cancelled = cancel_job(tmp_path, job["id"])
        assert cancelled["status"] == "cancelled"
        assert cancelled["steps"][0]["status"] == "cancelled"
        # Terminal manifests are left alone.
        assert cancel_job(tmp_path, job["id"])["status"] == "cancelled"

    def test_a_cancelled_job_can_be_resumed(self, tmp_path):
        job = create_job(tmp_path, "assemble", ["render"], payload={})
        cancel_job(tmp_path, job["id"])

        async def render(payload, manifest):
            return {"ok": True}

        manifest = asyncio.run(run_job(tmp_path, job["id"], {"render": render}))
        assert manifest["status"] == "completed"
        assert manifest["steps"][0]["status"] == "completed"

    def test_start_job_is_idempotent_while_running(self, tmp_path):
        async def scenario():
            job = create_job(tmp_path, "assemble", ["render"], payload={})
            gate = asyncio.Event()
            runs = []

            async def render(payload, manifest):
                runs.append(1)
                await gate.wait()
                return {"ok": True}

            start_job(tmp_path, job["id"], {"render": render})
            start_job(tmp_path, job["id"], {"render": render})
            await asyncio.sleep(0.02)
            gate.set()
            await wait_for_job(job["id"], timeout_s=5)
            assert runs == [1]

        asyncio.run(asyncio.wait_for(scenario(), timeout=10))

    def test_progress_writes_are_throttled_but_the_final_one_lands(self, tmp_path, monkeypatch):
        monkeypatch.setattr(jobs_module, "_PROGRESS_WRITE_INTERVAL_S", 60.0)
        seen: list[float | None] = []

        async def scenario():
            job = create_job(tmp_path, "assemble", ["render"], payload={})

            async def render(payload, manifest):
                context = job_context(manifest["id"])
                for fraction in (0.1, 0.2, 0.3):
                    context.report_progress({"fraction": fraction, "out_time_s": fraction})
                context.report_progress({"fraction": 1.0, "out_time_s": 1.0, "done": True})
                return {"ok": True}

            def notify(manifest):
                progress = manifest.get("progress") or {}
                seen.append(progress.get("fraction"))

            return await run_job(tmp_path, job["id"], {"render": render}, notify=notify)

        manifest = asyncio.run(scenario())
        assert manifest["status"] == "completed"
        # 0.1 is written (first report), 0.2/0.3 are throttled, 1.0 is final.
        assert 0.1 in seen and 0.2 not in seen and 0.3 not in seen and 1.0 in seen


class TestJobContext:
    def test_report_progress_without_a_sink_is_a_noop(self):
        context = JobContext(job_id="x")
        context.report_progress({"fraction": 0.5})
        assert context.cancelled is False


class TestNotify:
    def test_publish_puts_a_montage_event_on_the_bus(self):
        from navin.bus.outbound_events import MontageUpdatedEvent, outbound_event_from_message
        from navin.bus.queue import MessageBus
        from navin.montage.notify import job_notifier, publish_montage_update

        async def scenario():
            bus = MessageBus()
            publish_montage_update(bus, "/tmp/proj", kind="timeline", name="demo")
            message = bus.outbound.get_nowait()
            event = outbound_event_from_message(message)
            assert isinstance(event, MontageUpdatedEvent)
            assert event.kind == "timeline" and event.name == "demo"
            assert event.project_path == "/tmp/proj"

            notify = job_notifier(bus, "/tmp/proj")
            notify(
                {
                    "id": "j1",
                    "operation": "timeline-render",
                    "status": "completed",
                    "steps": [{"result": {"output": "out.mp4"}}],
                    "payload": {"timeline": "demo"},
                }
            )
            first = outbound_event_from_message(bus.outbound.get_nowait())
            second = outbound_event_from_message(bus.outbound.get_nowait())
            assert isinstance(first, MontageUpdatedEvent) and first.kind == "job"
            assert first.job["output"] == "out.mp4" and first.job["timeline"] == "demo"
            assert isinstance(second, MontageUpdatedEvent) and second.kind == "assets"

        asyncio.run(scenario())

    def test_publish_without_a_bus_is_harmless(self):
        from navin.montage.notify import publish_montage_update

        publish_montage_update(None, None, kind="assets")


class TestImportMedia:
    def test_equal_names_and_sizes_do_not_substitute_a_different_clip(self, tmp_path):
        root = tmp_path / "project"
        root.mkdir()
        first = tmp_path / "first" / "clip.mp4"
        second = tmp_path / "second" / "clip.mp4"
        first.parent.mkdir()
        second.parent.mkdir()
        first.write_bytes(b"first-take")
        second.write_bytes(b"other-take")
        first_path = import_media(root, first)
        second_path = import_media(root, second)
        assert first_path != second_path
        assert (root / first_path).read_bytes() == b"first-take"
        assert (root / second_path).read_bytes() == b"other-take"
        second.write_bytes(b"third-take")
        third_path = import_media(root, second)
        assert third_path not in {first_path, second_path}
        assert (root / second_path).read_bytes() == b"other-take"
        assert (root / third_path).read_bytes() == b"third-take"

    def test_files_inside_the_workspace_are_left_in_place(self, tmp_path):
        inside = tmp_path / "media" / "a.mp4"
        inside.parent.mkdir()
        inside.write_bytes(b"a")
        assert import_media(tmp_path, inside) == "media/a.mp4"

    def test_outside_files_are_copied_under_the_montage_kit(self, tmp_path):
        root = tmp_path / "project"
        root.mkdir()
        outside = tmp_path / "generated" / "clip.mp4"
        outside.parent.mkdir()
        outside.write_bytes(b"clip-one")
        rel = import_media(root, outside)
        assert rel == "marketing/montage/creatives/clip.mp4"
        assert (root / rel).read_bytes() == b"clip-one"
        # Same file again: reused, not duplicated.
        assert import_media(root, outside) == rel
        # A different file with the same name gets a distinct destination.
        other = tmp_path / "elsewhere" / "clip.mp4"
        other.parent.mkdir()
        other.write_bytes(b"different-content-here")
        second = import_media(root, other)
        assert second != rel and (root / second).is_file()


class TestMontageToolTimeline:
    def _tool(self, workspace):
        from navin.agent.tools.context import ToolContext
        from navin.agent.tools.montage import MontageTool
        from navin.config.schema import ToolsConfig

        return MontageTool.create(ToolContext(config=ToolsConfig(), workspace=str(workspace)))

    def _fixtures(self, root: Path) -> tuple[Path, Path]:
        clip = root / "marketing" / "montage" / "demos" / "demo.mp4"
        clip.parent.mkdir(parents=True)
        clip.write_bytes(b"\x00" * 64)
        still = root / "hero.png"
        still.write_bytes(b"\x89PNG\r\n\x1a\n")
        return clip, still

    def test_timeline_actions_are_offered(self, tmp_path):
        schema = self._tool(tmp_path).to_schema()
        actions = schema["function"]["parameters"]["properties"]["action"]["enum"]
        for action in (
            "probe",
            "timeline_list",
            "timeline_get",
            "timeline_save",
            "timeline_render",
            "timeline_delete",
            "cancel_job",
        ):
            assert action in actions

    def test_save_get_list_delete_round_trip(self, tmp_path, monkeypatch):
        self._fixtures(tmp_path)
        tool = self._tool(tmp_path)

        async def no_probe(spec, ffmpeg, timeout_s=20.0):
            return {}

        monkeypatch.setattr("navin.montage.assemble.probe_video_lengths", no_probe)
        saved = json.loads(
            asyncio.run(
                tool.execute(
                    action="timeline_save",
                    name="launch",
                    visuals="marketing/montage/demos/demo.mp4,hero.png",
                    durations="0,3",
                    trims="1-4,-",
                    transition="fade",
                    transition_duration=0.4,
                    profile="youtube_shorts",
                )
            )
        )
        assert saved["name"] == "launch"
        assert [v["path"] for v in saved["visuals"]] == ["marketing/montage/demos/demo.mp4", "hero.png"]
        assert saved["visuals"][0]["start"] == 1.0 and saved["visuals"][0]["end"] == 4.0
        assert saved["visuals"][1]["duration"] == 3.0
        assert (saved["width"], saved["height"]) == (1080, 1920)
        assert saved["transition"] == "fade"
        assert saved["output"] == "marketing/montage/exports/launch.mp4"
        assert "timeline_render" in saved["next_step"]
        assert get_timeline(tmp_path, "launch")["name"] == "launch"

        listed = json.loads(asyncio.run(tool.execute(action="timeline_list")))
        assert listed["count"] == 1 and listed["timelines"][0]["name"] == "launch"

        fetched = json.loads(asyncio.run(tool.execute(action="timeline_get", name="launch")))
        assert fetched["clip_durations_s"] == [3.0, 3.0]
        assert fetched["duration_s"] == pytest.approx(5.6)

        deleted = json.loads(asyncio.run(tool.execute(action="timeline_delete", name="launch")))
        assert deleted == {"ok": True, "name": "launch"}
        assert json.loads(asyncio.run(tool.execute(action="timeline_list")))["count"] == 0

    def test_generated_media_outside_the_project_is_imported(self, tmp_path, monkeypatch):
        root = tmp_path / "project"
        root.mkdir()
        media_dir = tmp_path / "media"
        media_dir.mkdir()
        generated = media_dir / "gen.mp4"
        generated.write_bytes(b"\x00" * 32)
        # The tool accepts inputs from the shared navin media directory (where
        # generate_video writes) and copies them into the project on save.
        monkeypatch.setattr("navin.config.paths.get_media_dir", lambda channel=None: media_dir)
        tool = self._tool(root)
        saved_raw = asyncio.run(
            tool.execute(action="timeline_save", name="gen", visuals=str(generated), durations="0")
        )
        assert not saved_raw.startswith("timeline_save error"), saved_raw
        saved = json.loads(saved_raw)
        assert saved["visuals"][0]["path"] == "marketing/montage/creatives/gen.mp4"
        assert (root / "marketing/montage/creatives/gen.mp4").is_file()

    def test_missing_arguments_read_as_instructions(self, tmp_path):
        tool = self._tool(tmp_path)
        assert "name=" in asyncio.run(tool.execute(action="timeline_get"))
        assert "visuals=" in asyncio.run(tool.execute(action="timeline_save", name="x"))
        assert "path=" in asyncio.run(tool.execute(action="probe"))
        assert "error" in asyncio.run(tool.execute(action="timeline_get", name="nope"))

    def test_probe_action_reports_a_workspace_file(self, tmp_path, monkeypatch):
        clip, _still = self._fixtures(tmp_path)
        clear_probe_cache()

        async def fake_probe(path, **kwargs):
            return MediaInfo(path=str(path), kind="video", size_bytes=64, duration=8.0).to_dict()

        monkeypatch.setattr("navin.montage.probe.probe_media", fake_probe)
        out = json.loads(
            asyncio.run(self._tool(tmp_path).execute(action="probe", path="marketing/montage/demos/demo.mp4"))
        )
        assert out["duration"] == 8.0
        assert out["path"] == "marketing/montage/demos/demo.mp4"

    def test_timeline_render_reports_the_job_and_streams_progress(self, tmp_path, monkeypatch):
        clip, _still = self._fixtures(tmp_path)
        spec = spec_from_paths(
            visuals=[str(clip)],
            output=str(tmp_path / "marketing/montage/exports/x.mp4"),
            width=160,
            height=120,
        )
        put_timeline(tmp_path, "x", timeline_from_spec(spec, tmp_path, name="x"))
        hooks: dict = {}

        async def fake_assemble(spec, **kwargs):
            hooks.update(kwargs)
            kwargs["on_progress"](RenderProgress(out_time_s=1.0, fraction=0.5))
            return {"ok": True, "output": spec.output, "duration_s": 2.0}

        monkeypatch.setattr("navin.montage.timeline.run_assemble", fake_assemble)
        out = json.loads(
            asyncio.run(self._tool(tmp_path).execute(action="timeline_render", name="x"))
        )
        assert out["ok"] is True
        assert out["job_status"] == "completed"
        assert out["output"] == "marketing/montage/exports/x.mp4"
        assert "message tool" in out["next_step"]
        assert callable(hooks.get("on_progress"))
        manifest = get_job(tmp_path, out["job_id"])
        assert manifest["progress"]["fraction"] == 0.5

    def test_cancel_job_action(self, tmp_path):
        job = create_job(tmp_path, "assemble", ["render"], payload={})
        out = json.loads(
            asyncio.run(self._tool(tmp_path).execute(action="cancel_job", job_id=job["id"]))
        )
        assert out["status"] == "cancelled"
        assert "requires job_id" in asyncio.run(self._tool(tmp_path).execute(action="cancel_job"))


class TestAssetsApi:
    def test_gallery_rows_carry_timeline_kind_and_cached_probe(self, tmp_path):
        from navin.webui.montage_api import list_montage_assets

        demos = tmp_path / "marketing" / "montage" / "demos"
        demos.mkdir(parents=True)
        (demos / "a.mp4").write_bytes(b"\x00")
        (demos / "anim.gif").write_bytes(b"\x00")
        (demos / "logo.svg").write_text("<svg/>")
        (demos / "notes.srt").write_text("1\n")
        rows = {row["name"]: row for row in list_montage_assets(tmp_path)["assets"]}
        assert rows["a.mp4"]["kind"] == "video" and rows["a.mp4"]["visual"] == "video"
        # A gif previews as an image but is cut like a silent clip.
        assert rows["anim.gif"]["kind"] == "image" and rows["anim.gif"]["visual"] == "video"
        assert rows["logo.svg"]["kind"] == "image" and rows["logo.svg"]["visual"] is None
        assert rows["notes.srt"]["kind"] == "document"
        assert "duration" not in rows["a.mp4"]

    def test_probe_endpoint_helper_rejects_paths_outside_the_workspace(self, tmp_path):
        from navin.webui.montage_api import MontageApiError, probe_montage_media

        with pytest.raises(MontageApiError) as escape:
            asyncio.run(probe_montage_media(tmp_path, "../outside.mp4"))
        assert escape.value.status == 400
        with pytest.raises(MontageApiError) as missing:
            asyncio.run(probe_montage_media(tmp_path, "nope.mp4"))
        assert missing.value.status == 404
        with pytest.raises(MontageApiError):
            asyncio.run(probe_montage_media(tmp_path, ""))
