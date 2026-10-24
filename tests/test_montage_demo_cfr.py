# Copyright (c) 2026-present Navinspire IA
# SPDX-License-Identifier: AGPL-3.0-only

"""Demo masters are constant frame rate, visually lossless x264."""

from __future__ import annotations

import json
import shutil
import subprocess
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from navin.montage import demo


class CfrArgsTest(unittest.TestCase):
    def test_transcode_args_force_cfr_and_quality(self) -> None:
        args = demo.cfr_transcode_args("ffmpeg", Path("/in.webm"), Path("/out.mp4"))
        self.assertEqual(args[:4], ["ffmpeg", "-y", "-i", "/in.webm"])
        self.assertEqual(args[-1], "/out.mp4")
        self.assertEqual(args[args.index("-c:v") + 1], "libx264")
        self.assertEqual(args[args.index("-crf") + 1], "16")
        self.assertEqual(args[args.index("-preset") + 1], "slow")
        self.assertEqual(args[args.index("-r") + 1], "30")
        self.assertTrue(args[args.index("-vf") + 1].startswith("fps=30,"))
        self.assertEqual(args[args.index("-pix_fmt") + 1], "yuv420p")
        self.assertIn("+faststart", args)

    def test_fps_is_30_or_60(self) -> None:
        self.assertEqual(demo.demo_fps(None), 30)
        self.assertEqual(demo.demo_fps(24), 30)
        self.assertEqual(demo.demo_fps(60), 60)
        self.assertEqual(demo.demo_fps("bad"), 30)
        args = demo.frames_encode_args("ffmpeg", Path("/l.txt"), Path("/o.mp4"), fps=60)
        self.assertEqual(args[args.index("-f") + 1], "concat")
        self.assertEqual(args[args.index("-r") + 1], "60")
        self.assertIn("-an", args)

    def test_transcode_uses_the_cfr_args(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            source = Path(tmp) / "a.webm"
            source.write_bytes(b"x")
            calls = []

            def fake_run(argv, timeout_s):
                calls.append(argv)
                Path(argv[-1]).write_bytes(b"mp4")
                return mock.Mock(ok=True)

            with mock.patch("navin.montage.detect.find_ffmpeg", return_value="ffmpeg"), \
                    mock.patch.object(demo, "run_process_sync", side_effect=fake_run):
                out = demo.transcode_to_mp4(source, fps=60)
            self.assertEqual(out, source.with_suffix(".mp4"))
            self.assertIn("-crf", calls[0])
            self.assertEqual(calls[0][calls[0].index("-r") + 1], "60")

    def test_frame_list_holds_each_frame_until_the_next(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            frames = [(Path(tmp) / "b.jpg", 0.5), (Path(tmp) / "a.jpg", 0.2), (Path(tmp) / "c.jpg", 1.5)]
            listing = demo.write_frame_concat(frames, 2.0, Path(tmp) / "l.txt").read_text().splitlines()
            self.assertEqual(listing[0], "ffconcat version 1.0")
            durations = [float(line.split()[1]) for line in listing if line.startswith("duration")]
            # First frame starts at t=0 so video time equals action-log time.
            self.assertEqual(durations, [0.5, 1.0, 0.5])
            self.assertTrue(listing[-1].endswith("c.jpg'"))


@unittest.skipUnless(shutil.which("ffmpeg") and shutil.which("ffprobe"), "ffmpeg not installed")
class FrameEncodeTest(unittest.TestCase):
    def test_sparse_frames_become_a_cfr_video_of_the_right_length(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            frames = []
            for index, (color, t) in enumerate((("red", 0.0), ("blue", 0.4), ("green", 1.3))):
                path = root / f"f{index}.jpg"
                subprocess.run(
                    ["ffmpeg", "-v", "error", "-y", "-f", "lavfi", "-i", f"color=c={color}:s=320x180",
                     "-frames:v", "1", str(path)],
                    check=True,
                )
                frames.append((path, t))
            out = demo.encode_frames_to_mp4(frames, root / "demo.mp4", end_time=2.0, fps=30)
            self.assertIsNotNone(out)
            probe = subprocess.run(
                ["ffprobe", "-v", "error", "-select_streams", "v:0", "-count_frames", "-show_entries",
                 "stream=r_frame_rate,avg_frame_rate,nb_read_frames,codec_name", "-of", "json", str(out)],
                capture_output=True, text=True, check=True,
            )
            stream = json.loads(probe.stdout)["streams"][0]
            self.assertEqual(stream["codec_name"], "h264")
            self.assertEqual(stream["r_frame_rate"], "30/1")
            self.assertEqual(stream["avg_frame_rate"], "30/1")
            self.assertAlmostEqual(int(stream["nb_read_frames"]), 60, delta=2)
            self.assertFalse((root / "demo.frames.txt").exists())


if __name__ == "__main__":
    unittest.main()
