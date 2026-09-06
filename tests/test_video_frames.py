"""Video attachments must reach vision models as frames, or say why they cannot."""

from __future__ import annotations

from pathlib import Path

import pytest

from navin.utils import video_frames
from navin.utils.video_frames import (
    VideoFrames,
    describe_frames,
    expand_video_attachments,
    extract_video_frames,
    frame_timestamps,
    is_video_path,
    parse_duration,
)


class TestIsVideoPath:
    def test_recognizes_common_containers(self):
        for name in ("clip.mp4", "a.MOV", "rec.webm", "x.mkv", "y.avi", "z.m4v"):
            assert is_video_path(name), name

    def test_rejects_images_and_documents(self):
        for name in ("shot.png", "a.jpg", "doc.pdf", "notes.md", "archive.zip"):
            assert not is_video_path(name), name

    def test_ignores_query_and_fragment(self):
        assert is_video_path("https://cdn/clip.mp4?sig=abc#t=10")


class TestParseDuration:
    def test_reads_ffmpeg_banner(self):
        stderr = "  Duration: 00:01:23.45, start: 0.000000, bitrate: 1200 kb/s"
        assert parse_duration(stderr) == pytest.approx(83.45)

    def test_handles_hours(self):
        assert parse_duration("Duration: 01:00:02.00,") == pytest.approx(3602.0)

    def test_returns_none_without_banner(self):
        assert parse_duration("") is None
        assert parse_duration("no duration here") is None


class TestFrameTimestamps:
    def test_samples_inside_the_clip_not_at_the_bounds(self):
        stamps = frame_timestamps(10.0, 4)
        assert len(stamps) == 4
        assert stamps[0] > 0
        assert stamps[-1] < 10.0
        assert stamps == sorted(stamps)

    def test_single_frame_takes_the_middle(self):
        assert frame_timestamps(10.0, 1) == [5.0]

    def test_unknown_duration_falls_back_to_first_frame(self):
        assert frame_timestamps(None, 6) == [0.0]
        assert frame_timestamps(0, 6) == [0.0]

    def test_count_is_clamped(self):
        assert len(frame_timestamps(60.0, 999)) == video_frames.MAX_FRAMES_CEILING
        assert len(frame_timestamps(60.0, 0)) == 1


class TestExtractVideoFrames:
    def test_missing_file_is_reported_not_raised(self, tmp_path: Path):
        result = extract_video_frames(tmp_path / "nope.mp4")
        assert not result.ok
        assert result.reason == "file not found"

    def test_missing_ffmpeg_is_reported_not_raised(self, tmp_path: Path, monkeypatch):
        video = tmp_path / "clip.mp4"
        video.write_bytes(b"\x00\x00\x00\x18ftypmp42")
        monkeypatch.setattr(video_frames, "_find_ffmpeg", lambda: None)
        result = extract_video_frames(video)
        assert not result.ok
        assert "ffmpeg" in (result.reason or "")

    def test_writes_one_image_per_sample_point(self, tmp_path: Path, monkeypatch):
        video = tmp_path / "clip.mp4"
        video.write_bytes(b"\x00\x00\x00\x18ftypmp42")
        monkeypatch.setattr(video_frames, "_find_ffmpeg", lambda: "/usr/bin/ffmpeg")
        monkeypatch.setattr(video_frames, "probe_duration", lambda *a, **k: 12.0)

        calls: list[list[str]] = []

        def fake_run(args, timeout):
            calls.append(args)
            Path(args[-1]).write_bytes(b"\xff\xd8\xffjpeg")
            return type("Proc", (), {"returncode": 0, "stderr": ""})()

        monkeypatch.setattr(video_frames, "_run", fake_run)
        result = extract_video_frames(video, max_frames=3)

        assert result.ok
        assert len(result.paths) == 3
        assert len(result.timestamps) == 3
        assert result.duration == 12.0
        assert all(Path(p).is_file() for p in result.paths)
        # Seeking happens before -i so extraction stays fast on long clips.
        for args in calls:
            assert args.index("-ss") < args.index("-i")

    def test_falls_back_to_png_when_the_build_has_no_jpeg_encoder(
        self, tmp_path: Path, monkeypatch
    ):
        video = tmp_path / "clip.mp4"
        video.write_bytes(b"\x00\x00\x00\x18ftypmp42")
        monkeypatch.setattr(video_frames, "_find_ffmpeg", lambda: "/usr/bin/ffmpeg")
        monkeypatch.setattr(video_frames, "probe_duration", lambda *a, **k: 4.0)

        def fake_run(args, timeout):
            target = Path(args[-1])
            if target.suffix == ".jpg":
                return type("Proc", (), {"returncode": 1, "stderr": "no mjpeg encoder"})()
            target.write_bytes(b"\x89PNG\r\n\x1a\n")
            return type("Proc", (), {"returncode": 0, "stderr": ""})()

        monkeypatch.setattr(video_frames, "_run", fake_run)
        result = extract_video_frames(video, max_frames=2, base_dir=tmp_path / "cache")

        assert result.ok
        assert all(path.endswith(".png") for path in result.paths)

    def test_a_timeout_stops_the_whole_sampling(self, tmp_path: Path, monkeypatch):
        import subprocess

        video = tmp_path / "clip.mp4"
        video.write_bytes(b"\x00\x00\x00\x18ftypmp42")
        monkeypatch.setattr(video_frames, "_find_ffmpeg", lambda: "/usr/bin/ffmpeg")
        monkeypatch.setattr(video_frames, "probe_duration", lambda *a, **k: 600.0)

        calls = {"count": 0}

        def fake_run(args, timeout):
            calls["count"] += 1
            raise subprocess.TimeoutExpired(cmd="ffmpeg", timeout=timeout)

        monkeypatch.setattr(video_frames, "_run", fake_run)
        result = extract_video_frames(video, max_frames=6, base_dir=tmp_path / "cache")

        assert not result.ok
        assert result.reason == "frame extraction timed out"
        # One attempt, not six: the turn must not stall on a pathological file.
        assert calls["count"] == 1

    def test_failed_decode_reports_reason(self, tmp_path: Path, monkeypatch):
        video = tmp_path / "clip.mp4"
        video.write_bytes(b"broken")
        monkeypatch.setattr(video_frames, "_find_ffmpeg", lambda: "/usr/bin/ffmpeg")
        monkeypatch.setattr(video_frames, "probe_duration", lambda *a, **k: None)
        monkeypatch.setattr(
            video_frames,
            "_run",
            lambda args, timeout: type("Proc", (), {"returncode": 1, "stderr": "bad"})(),
        )
        result = extract_video_frames(video)
        assert not result.ok
        assert "no frame" in (result.reason or "")

    def test_reuses_frames_already_on_disk(self, tmp_path: Path, monkeypatch):
        video = tmp_path / "clip.mp4"
        video.write_bytes(b"\x00\x00\x00\x18ftypmp42")
        monkeypatch.setattr(video_frames, "_find_ffmpeg", lambda: "/usr/bin/ffmpeg")
        monkeypatch.setattr(video_frames, "probe_duration", lambda *a, **k: 8.0)

        runs = {"count": 0}

        def fake_run(args, timeout):
            runs["count"] += 1
            Path(args[-1]).write_bytes(b"\xff\xd8\xffjpeg")
            return type("Proc", (), {"returncode": 0, "stderr": ""})()

        monkeypatch.setattr(video_frames, "_run", fake_run)
        first = extract_video_frames(video, max_frames=2, base_dir=tmp_path / "cache")
        after_first = runs["count"]
        second = extract_video_frames(video, max_frames=2, base_dir=tmp_path / "cache")

        assert first.paths == second.paths
        assert runs["count"] == after_first

    def test_a_different_sample_count_does_not_reuse_stale_frames(
        self, tmp_path: Path, monkeypatch
    ):
        video = tmp_path / "clip.mp4"
        video.write_bytes(b"\x00\x00\x00\x18ftypmp42")
        monkeypatch.setattr(video_frames, "_find_ffmpeg", lambda: "/usr/bin/ffmpeg")
        monkeypatch.setattr(video_frames, "probe_duration", lambda *a, **k: 8.0)
        monkeypatch.setattr(
            video_frames,
            "_run",
            lambda args, timeout: (
                Path(args[-1]).write_bytes(b"\xff\xd8\xffjpeg"),
                type("Proc", (), {"returncode": 0, "stderr": ""})(),
            )[1],
        )
        cache = tmp_path / "cache"
        two = extract_video_frames(video, max_frames=2, base_dir=cache)
        three = extract_video_frames(video, max_frames=3, base_dir=cache)

        # Different timestamps must not land on the same cached files.
        assert two.timestamps != three.timestamps
        assert set(two.paths).isdisjoint(three.paths)

    def test_frames_are_not_written_into_the_video_directory(
        self, tmp_path: Path, monkeypatch
    ):
        video = tmp_path / "repo" / "clip.mp4"
        video.parent.mkdir()
        video.write_bytes(b"\x00\x00\x00\x18ftypmp42")
        monkeypatch.setattr(video_frames, "_find_ffmpeg", lambda: "/usr/bin/ffmpeg")
        monkeypatch.setattr(video_frames, "probe_duration", lambda *a, **k: 4.0)
        monkeypatch.setattr(
            video_frames,
            "frames_cache_root",
            lambda: tmp_path / "cache",
        )
        monkeypatch.setattr(
            video_frames,
            "_run",
            lambda args, timeout: (
                Path(args[-1]).write_bytes(b"\xff\xd8\xffjpeg"),
                type("Proc", (), {"returncode": 0, "stderr": ""})(),
            )[1],
        )
        result = extract_video_frames(video, max_frames=2)

        assert result.ok
        assert list(video.parent.iterdir()) == [video]
        assert all(str(tmp_path / "cache") in path for path in result.paths)


class TestDescribeFrames:
    def test_states_the_limits_so_the_model_cannot_overclaim(self):
        note = describe_frames(
            VideoFrames(
                source="/tmp/demo.mp4",
                paths=["/tmp/f0.jpg", "/tmp/f1.jpg"],
                timestamps=[1.0, 2.0],
                duration=4.0,
            )
        )
        assert "demo.mp4" in note
        assert "2 sampled frames" in note
        assert "motion between frames is not available" in note
        # The soundtrack has its own note now; this one must not deny it.
        assert "soundtrack is reported in a separate note" in note

    def test_failure_note_names_the_reason(self):
        note = describe_frames(VideoFrames(source="/tmp/x.mp4", reason="ffmpeg is not installed"))
        assert "not analyzable" in note
        assert "ffmpeg is not installed" in note


class TestExpandVideoAttachments:
    def test_non_video_media_passes_through_untouched(self):
        media = ["/tmp/a.png", "/tmp/notes.pdf"]
        text, out = expand_video_attachments("hello", media)
        assert text == "hello"
        assert out == media

    def test_video_becomes_frames_and_a_note(self, monkeypatch):
        monkeypatch.setattr(
            video_frames,
            "extract_video_frames",
            lambda path, **kw: VideoFrames(
                source=path,
                paths=["/tmp/f0.jpg", "/tmp/f1.jpg"],
                timestamps=[1.0, 3.0],
                duration=5.0,
            ),
        )
        text, media = expand_video_attachments("look", ["/tmp/a.png", "/tmp/clip.mp4"])
        assert media == ["/tmp/a.png", "/tmp/f0.jpg", "/tmp/f1.jpg"]
        assert "look" in text
        assert "clip.mp4" in text

    def test_unanalyzable_video_is_announced_not_silently_dropped(self, monkeypatch):
        monkeypatch.setattr(
            video_frames,
            "extract_video_frames",
            lambda path, **kw: VideoFrames(source=path, reason="ffmpeg is not installed"),
        )
        text, media = expand_video_attachments("check this", ["/tmp/clip.mp4"])
        assert media == []
        assert "not analyzable" in text
        assert "ffmpeg is not installed" in text

    def test_empty_media_is_a_noop(self):
        assert expand_video_attachments("hi", []) == ("hi", [])


class TestPipelineOrder:
    """The frames must survive the document/attachment step that runs next."""

    def test_frames_reach_the_image_list_through_extract_documents(
        self, tmp_path: Path, monkeypatch
    ):
        from navin.utils.document import extract_documents

        frame_a = tmp_path / "f0.jpg"
        frame_b = tmp_path / "f1.jpg"
        for frame in (frame_a, frame_b):
            frame.write_bytes(b"\xff\xd8\xff" + b"\x00" * 32)

        monkeypatch.setattr(
            video_frames,
            "extract_video_frames",
            lambda path, **kw: VideoFrames(
                source=path,
                paths=[str(frame_a), str(frame_b)],
                timestamps=[1.0, 2.0],
                duration=4.0,
            ),
        )

        text, media = expand_video_attachments("watch", [str(tmp_path / "clip.mp4")])
        text, images = extract_documents(text, media)

        assert images == [str(frame_a), str(frame_b)]
        assert "sampled frames" in text

    def test_frames_reach_the_image_list_through_reference_attachments(
        self, tmp_path: Path, monkeypatch
    ):
        from navin.utils.document import reference_non_image_attachments

        frame = tmp_path / "f0.jpg"
        frame.write_bytes(b"\xff\xd8\xff" + b"\x00" * 32)
        monkeypatch.setattr(
            video_frames,
            "extract_video_frames",
            lambda path, **kw: VideoFrames(
                source=path, paths=[str(frame)], timestamps=[1.0], duration=2.0
            ),
        )

        text, media = expand_video_attachments("watch", [str(tmp_path / "clip.mp4")])
        text, images = reference_non_image_attachments(text, media)

        assert images == [str(frame)]
        assert "[Attachment:" not in text
