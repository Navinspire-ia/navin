# Copyright (c) 2026-present Navinspire IA
# SPDX-License-Identifier: AGPL-3.0-only

"""Full video assembly: clips plus stills plus music plus narration into one MP4.

The filter graph is the part that breaks in practice, so it is asserted directly
rather than through ffmpeg: mismatched sample aspect ratio silently corrupts a
concat, a looped music bed silently produces an endless file, and a fixed volume
cut makes narration unintelligible. Each of those has a test here.
"""

from __future__ import annotations

import asyncio
from pathlib import Path

import pytest

from navin.montage.assemble import (
    DEFAULT_IMAGE_DURATION,
    DEFAULT_MUSIC_GAIN_DB,
    DEFAULT_TRANSITION_DURATION,
    KIND_IMAGE,
    KIND_VIDEO,
    AssembleError,
    AssembleSpec,
    VisualClip,
    build_audio_filters,
    build_ffmpeg_args,
    build_filter_complex,
    build_video_filters,
    classify_visual,
    clip_play_duration,
    find_ffprobe,
    measure_total_duration,
    output_input_collision,
    parse_durations,
    parse_ffmpeg_duration,
    parse_path_list,
    parse_trims,
    probe_duration_with_ffmpeg,
    resolve_total_duration,
    run_assemble,
    spec_from_paths,
    staging_output_path,
    validate_spec,
)


def _spec(**overrides) -> AssembleSpec:
    base = {
        "visuals": (VisualClip.from_path("/m/clip.mp4"),),
        "output": "/m/out.mp4",
    }
    base.update(overrides)
    return AssembleSpec(**base)


def _indices(spec: AssembleSpec) -> dict[str, int]:
    from navin.montage.assemble import _input_args

    return _input_args(spec)[1]


class TestClassification:
    @pytest.mark.parametrize("path", ["a.png", "a.JPG", "a.jpeg", "a.webp", "a.tiff"])
    def test_images(self, path):
        assert classify_visual(path) == KIND_IMAGE

    @pytest.mark.parametrize("path", ["a.mp4", "a.MOV", "a.mkv", "a.webm"])
    def test_videos(self, path):
        assert classify_visual(path) == KIND_VIDEO

    @pytest.mark.parametrize("path", ["a.txt", "a.mp3", "a", "a.svg"])
    def test_anything_else_is_refused_early(self, path):
        with pytest.raises(AssembleError, match="unsupported visual format"):
            classify_visual(path)

    def test_a_still_gets_a_default_duration(self):
        clip = VisualClip.from_path("/m/frame.png")
        assert clip.kind == KIND_IMAGE
        assert clip.duration == DEFAULT_IMAGE_DURATION

    def test_an_explicit_still_duration_is_kept(self):
        assert VisualClip.from_path("/m/frame.png", 5.5).duration == 5.5

    def test_a_nonsense_still_duration_falls_back(self):
        assert VisualClip.from_path("/m/frame.png", 0).duration == DEFAULT_IMAGE_DURATION
        assert VisualClip.from_path("/m/frame.png", -2).duration == DEFAULT_IMAGE_DURATION

    def test_a_video_needs_no_duration(self):
        assert VisualClip.from_path("/m/clip.mp4").duration is None


class TestValidation:
    def test_no_visual_is_refused_with_an_actionable_message(self):
        with pytest.raises(AssembleError, match="at least one visual"):
            validate_spec(_spec(visuals=()))

    def test_missing_output(self):
        with pytest.raises(AssembleError, match="output path"):
            validate_spec(_spec(output=""))

    @pytest.mark.parametrize(("w", "h"), [(0, 100), (100, 0), (-1, 100)])
    def test_bad_canvas(self, w, h):
        with pytest.raises(AssembleError, match="positive"):
            validate_spec(_spec(width=w, height=h))

    @pytest.mark.parametrize("fps", [0, -1, 121])
    def test_bad_fps(self, fps):
        with pytest.raises(AssembleError, match="fps"):
            validate_spec(_spec(fps=fps))

    def test_a_still_without_duration_cannot_slip_through(self):
        bad = VisualClip(path="/m/frame.png", kind=KIND_IMAGE, duration=None)
        with pytest.raises(AssembleError, match="positive duration"):
            validate_spec(_spec(visuals=(bad,)))

    def test_a_valid_spec_passes(self):
        validate_spec(_spec())

    def test_an_image_cannot_be_trimmed(self):
        bad = VisualClip.from_path("/m/frame.png", 2, start=1.0)
        with pytest.raises(AssembleError, match="cannot be trimmed"):
            validate_spec(_spec(visuals=(bad,)))

    def test_a_negative_trim_start_is_refused(self):
        bad = VisualClip.from_path("/m/clip.mp4", start=-1.0)
        with pytest.raises(AssembleError, match="negative"):
            validate_spec(_spec(visuals=(bad,)))

    def test_a_trim_that_ends_before_it_starts_is_refused(self):
        bad = VisualClip.from_path("/m/clip.mp4", start=5.0, end=2.0)
        with pytest.raises(AssembleError, match="after trim start"):
            validate_spec(_spec(visuals=(bad,)))

    def test_an_unknown_transition_is_refused_with_the_valid_names(self):
        with pytest.raises(AssembleError, match="unknown transition"):
            validate_spec(_spec(transition="starwipe"))

    @pytest.mark.parametrize("fade", [0, -1, 5.1])
    def test_a_nonsense_transition_length_is_refused(self, fade):
        two = (VisualClip.from_path("/m/a.mp4"), VisualClip.from_path("/m/b.mp4"))
        with pytest.raises(AssembleError, match="transition_duration"):
            validate_spec(_spec(visuals=two, transition="fade", transition_duration=fade))

    @pytest.mark.parametrize("gain", [-61, 13])
    def test_an_out_of_range_music_gain_is_refused(self, gain):
        with pytest.raises(AssembleError, match="music_gain_db"):
            validate_spec(_spec(music="/m/bed.mp3", music_gain_db=gain))

    def test_rendering_an_export_back_into_itself_is_refused(self, tmp_path):
        # The previous export shows up in the asset library, so a user can add
        # it as a clip while the output path still points at it. ffmpeg cannot
        # edit in place and the failure cleanup used to delete the source.
        export = tmp_path / "exports" / "final.mp4"
        spec = _spec(
            visuals=(VisualClip.from_path("/m/a.mp4"), VisualClip.from_path(str(export))),
            output=str(tmp_path / "exports" / ".." / "exports" / "final.mp4"),
        )
        with pytest.raises(AssembleError, match="also an input"):
            validate_spec(spec)

    def test_music_or_voice_at_the_output_path_is_refused_too(self, tmp_path):
        with pytest.raises(AssembleError, match="also an input"):
            validate_spec(_spec(output=str(tmp_path / "bed.mp3"), music=str(tmp_path / "bed.mp3")))

    def test_distinct_output_and_inputs_pass(self, tmp_path):
        assert output_input_collision(_spec(output=str(tmp_path / "out.mp4"))) is None


class TestStagedOutput:
    """The real path only changes once ffmpeg succeeded."""

    def _fake_runner(self, monkeypatch, *, ok: bool, write: bool, cancelled: bool = False):
        from navin.montage.ffmpeg_runner import ProcessResult

        seen: dict[str, object] = {}

        async def fake_run_process(args, *, timeout_s, on_stdout_line=None, cancel=None):
            seen["target"] = args[-1]
            if write:
                Path(args[-1]).write_bytes(b"new render")
            stderr = "" if ok else "Conversion failed!"
            return ProcessResult(
                ok=ok,
                argv=tuple(args),
                command=" ".join(args),
                exit_code=0 if ok else 1,
                stdout="",
                stderr=stderr,
                raw_stderr=stderr,
                duration_s=0.1,
                cancelled=cancelled,
            )

        monkeypatch.setattr("navin.montage.assemble.run_process", fake_run_process)
        monkeypatch.setattr("navin.montage.detect.find_ffmpeg", lambda: "/opt/ffmpeg")
        return seen

    def _still_spec(self, tmp_path) -> AssembleSpec:
        still = tmp_path / "frame.png"
        still.write_bytes(b"png")
        return spec_from_paths(
            visuals=[str(still)], durations=[1.0], output=str(tmp_path / "exports" / "final.mp4")
        )

    def test_ffmpeg_writes_a_hidden_sibling_that_replaces_the_output_on_success(
        self, tmp_path, monkeypatch
    ):
        seen = self._fake_runner(monkeypatch, ok=True, write=True)
        spec = self._still_spec(tmp_path)
        result = asyncio.run(run_assemble(spec))
        assert result["ok"] is True, result.get("error")
        target = Path(str(seen["target"]))
        assert target.parent == Path(spec.output).parent
        assert target.name.startswith(".final.part-") and target.suffix == ".mp4"
        assert not target.exists()
        assert Path(spec.output).read_bytes() == b"new render"
        assert result["output"] == spec.output
        assert result["size_bytes"] == len(b"new render")

    def test_a_failed_render_keeps_the_previous_export_and_leaves_no_partial(
        self, tmp_path, monkeypatch
    ):
        self._fake_runner(monkeypatch, ok=False, write=True)
        spec = self._still_spec(tmp_path)
        previous = Path(spec.output)
        previous.parent.mkdir(parents=True)
        previous.write_bytes(b"yesterday's export")
        result = asyncio.run(run_assemble(spec))
        assert result["ok"] is False
        assert "Conversion failed" in str(result["error"])
        assert previous.read_bytes() == b"yesterday's export"
        assert [p.name for p in previous.parent.iterdir()] == ["final.mp4"]

    def test_a_cancelled_render_keeps_the_previous_export(self, tmp_path, monkeypatch):
        self._fake_runner(monkeypatch, ok=False, write=True, cancelled=True)
        spec = self._still_spec(tmp_path)
        previous = Path(spec.output)
        previous.parent.mkdir(parents=True)
        previous.write_bytes(b"keep me")
        result = asyncio.run(run_assemble(spec, cancel=asyncio.Event()))
        assert result["cancelled"] is True
        assert previous.read_bytes() == b"keep me"
        assert [p.name for p in previous.parent.iterdir()] == ["final.mp4"]

    def test_the_staging_name_keeps_the_container_suffix(self):
        staged = staging_output_path(Path("/m/exports/final.mov"))
        assert staged.parent == Path("/m/exports")
        assert staged.suffix == ".mov"
        assert staged.name.startswith(".final.part-")


class TestVideoGraph:
    def test_every_visual_is_normalized_to_the_same_canvas_and_sar(self):
        # Without setsar and a shared scale, concat produces a corrupt stream.
        spec = _spec(
            visuals=(
                VisualClip.from_path("/m/a.mp4"),
                VisualClip.from_path("/m/b.png", 2),
            ),
            width=1920,
            height=1080,
            fps=25,
        )
        chains = build_video_filters(spec, _indices(spec))
        for position in (0, 1):
            chain = chains[position]
            assert "scale=1920:1080:force_original_aspect_ratio=decrease" in chain
            assert "pad=1920:1080" in chain
            assert "setsar=1" in chain
            assert "fps=25" in chain

    def test_several_visuals_are_concatenated_in_order(self):
        spec = _spec(
            visuals=(
                VisualClip.from_path("/m/a.mp4"),
                VisualClip.from_path("/m/b.png", 2),
                VisualClip.from_path("/m/c.mp4"),
            )
        )
        graph = build_filter_complex(spec, _indices(spec))
        assert "[v0][v1][v2]concat=n=3:v=1:a=0[vcat]" in graph

    def test_a_single_visual_skips_concat(self):
        graph = build_filter_complex(_spec(), _indices(_spec()))
        assert "concat=" not in graph
        assert "[vcat]" in graph

    def test_subtitles_are_burned_after_the_concat(self):
        spec = _spec(subtitles="/m/subs.srt")
        graph = build_filter_complex(spec, _indices(spec))
        assert "[vcat]subtitles=" in graph
        assert graph.endswith("[vout]") or "[vout]" in graph

    def test_a_windows_style_subtitle_path_is_escaped_for_the_filter(self):
        spec = _spec(subtitles=r"C:\media\subs.srt")
        graph = build_filter_complex(spec, _indices(spec))
        # A bare colon would end the filter option and break the whole graph.
        assert "C\\:/media/subs.srt" in graph

    def test_the_output_label_is_always_present(self):
        graph = build_filter_complex(_spec(), _indices(_spec()))
        assert "[vout]" in graph


class TestTrims:
    def test_a_trimmed_clip_seeks_on_the_input_side(self):
        # Input-side -ss decodes nothing before the cut; filter-side trim would.
        spec = _spec(visuals=(VisualClip.from_path("/m/clip.mp4", start=2.0, end=8.0),))
        args = build_ffmpeg_args(spec)
        at = args.index("-ss")
        assert args[at : at + 6] == ["-ss", "2", "-t", "6", "-i", "/m/clip.mp4"]

    def test_a_start_only_trim_drops_the_head_and_keeps_the_tail(self):
        spec = _spec(visuals=(VisualClip.from_path("/m/clip.mp4", start=3.0),))
        args = build_ffmpeg_args(spec)
        assert args[args.index("-ss") + 1] == "3"
        assert "-t" not in args

    def test_an_end_only_trim_keeps_the_head(self):
        spec = _spec(visuals=(VisualClip.from_path("/m/clip.mp4", end=5.0),))
        args = build_ffmpeg_args(spec)
        assert "-ss" not in args
        assert args[args.index("-t") + 1] == "5"

    def test_an_untrimmed_clip_gets_no_seek_flags(self):
        args = build_ffmpeg_args(_spec())
        assert "-ss" not in args

    def test_a_full_trim_needs_no_probing(self):
        clip = VisualClip.from_path("/m/clip.mp4", start=2.0, end=8.0)
        assert clip_play_duration(clip) == 6.0

    def test_a_trim_end_past_the_source_is_clamped_to_reality(self):
        # Otherwise the -t bound outlives the video and the bed plays over a
        # frozen last frame.
        clip = VisualClip.from_path("/m/clip.mp4", start=2.0, end=60.0)
        assert clip_play_duration(clip, {"/m/clip.mp4": 10.0}.get) == 8.0

    def test_a_start_only_trim_needs_the_source_length(self):
        clip = VisualClip.from_path("/m/clip.mp4", start=3.0)
        assert clip_play_duration(clip) is None
        assert clip_play_duration(clip, {"/m/clip.mp4": 10.0}.get) == 7.0

    def test_a_trim_that_swallows_the_whole_clip_is_unknown_not_zero(self):
        clip = VisualClip.from_path("/m/clip.mp4", start=10.0)
        assert clip_play_duration(clip, {"/m/clip.mp4": 5.0}.get) is None

    def test_the_timeline_uses_trimmed_lengths(self):
        spec = _spec(
            visuals=(
                VisualClip.from_path("/m/a.mp4", start=1.0, end=4.0),
                VisualClip.from_path("/m/b.png", 2),
            )
        )
        assert resolve_total_duration(spec) == 5.0


class TestTransitions:
    def _two(self, fade: float = 0.5) -> AssembleSpec:
        return _spec(
            visuals=(
                VisualClip.from_path("/m/a.mp4", 4.0),
                VisualClip.from_path("/m/b.mp4", 2.0),
            ),
            transition="fade",
            transition_duration=fade,
        )

    def test_two_clips_crossfade_at_the_end_of_the_first(self):
        spec = self._two()
        graph = ";".join(build_video_filters(spec, _indices(spec), [4.0, 2.0]))
        assert "[v0][v1]xfade=transition=fade:duration=0.5:offset=3.5[vcat]" in graph
        assert "concat=" not in graph

    def test_three_clips_chain_with_cumulative_offsets(self):
        spec = _spec(
            visuals=(
                VisualClip.from_path("/m/a.mp4", 4.0),
                VisualClip.from_path("/m/b.mp4", 2.0),
                VisualClip.from_path("/m/c.png", 3.0),
            ),
            transition="fade",
            transition_duration=0.5,
        )
        graph = ";".join(build_video_filters(spec, _indices(spec), [4.0, 2.0, 3.0]))
        assert "offset=3.5[x1]" in graph
        assert "[x1][v2]xfade" in graph
        assert "offset=5[vcat]" in graph

    def test_fading_streams_share_one_timebase(self):
        # xfade refuses inputs with different timebases; a decoded MP4 and a
        # looped still rarely agree, so the graph must pin settb.
        spec = self._two()
        graph = ";".join(build_video_filters(spec, _indices(spec), [4.0, 2.0]))
        assert "settb=AVTB" in graph

    def test_a_hard_cut_does_not_touch_the_timebase(self):
        graph = ";".join(build_video_filters(_spec(), _indices(_spec())))
        assert "settb" not in graph

    def test_one_visual_has_nothing_to_fade(self):
        spec = _spec(
            visuals=(VisualClip.from_path("/m/a.mp4", 4.0),), transition="fade"
        )
        graph = ";".join(build_video_filters(spec, _indices(spec), [4.0]))
        assert "xfade" not in graph

    def test_an_unmeasured_clip_refuses_the_fade_with_the_fix(self):
        spec = self._two()
        with pytest.raises(AssembleError, match="transition=none"):
            build_video_filters(spec, _indices(spec), [4.0, None])
        with pytest.raises(AssembleError, match="length of every clip"):
            build_video_filters(spec, _indices(spec), None)

    def test_a_fade_longer_than_a_clip_is_refused_not_rendered_broken(self):
        spec = self._two(fade=2.5)
        with pytest.raises(AssembleError, match="shortest clip"):
            build_video_filters(spec, _indices(spec), [4.0, 2.0])

    def test_each_crossfade_shortens_the_timeline(self):
        spec = self._two()
        assert resolve_total_duration(spec) == pytest.approx(5.5)

    def test_a_hard_cut_timeline_is_unchanged(self):
        spec = _spec(
            visuals=(
                VisualClip.from_path("/m/a.mp4", 4.0),
                VisualClip.from_path("/m/b.mp4", 2.0),
            )
        )
        assert resolve_total_duration(spec) == 6.0

    def test_the_full_command_carries_the_fade(self):
        spec = self._two()
        args = build_ffmpeg_args(spec, total_duration=5.5, clip_durations=[4.0, 2.0])
        graph = args[args.index("-filter_complex") + 1]
        assert "xfade=transition=fade" in graph
        assert args[args.index("-t") + 1] == "5.5"


class TestMusicGain:
    def test_the_bed_level_is_adjustable(self):
        spec = _spec(music="/m/bed.mp3", music_gain_db=-25.0)
        graph = ";".join(build_audio_filters(spec, _indices(spec)))
        assert "volume=-25.0dB" in graph

    def test_the_default_bed_level_keeps_headroom(self):
        spec = spec_from_paths(visuals=["/m/a.mp4"], output="/m/out.mp4", music="/m/bed.mp3")
        assert spec.music_gain_db == DEFAULT_MUSIC_GAIN_DB

    def test_a_zero_gain_is_a_choice_not_a_missing_value(self):
        spec = spec_from_paths(
            visuals=["/m/a.mp4"], output="/m/out.mp4",
            music="/m/bed.mp3", music_gain_db=0.0,
        )
        assert spec.music_gain_db == 0.0


class TestAudioGraph:
    def test_no_audio_means_no_audio_filters(self):
        assert build_audio_filters(_spec(), _indices(_spec())) == []

    def test_music_alone_is_laid_under_at_a_bed_level(self):
        spec = _spec(music="/m/bed.mp3", music_gain_db=-16.0)
        graph = ";".join(build_audio_filters(spec, _indices(spec)))
        assert "volume=-16.0dB" in graph
        assert "[aout]" in graph
        assert "sidechaincompress" not in graph

    def test_voice_alone_spans_the_edit_when_the_output_is_capped(self):
        spec = _spec(voice="/m/vo.mp3")
        graph = ";".join(build_audio_filters(spec, _indices(spec), bounded=True))
        assert "apad" in graph
        assert "[aout]" in graph

    def test_voice_alone_stays_finite_when_the_output_is_not_capped(self):
        spec = _spec(voice="/m/vo.mp3")
        graph = ";".join(build_audio_filters(spec, _indices(spec)))
        assert "apad" not in graph
        assert "[aout]" in graph

    def test_music_is_ducked_under_narration_rather_than_just_turned_down(self):
        spec = _spec(music="/m/bed.mp3", voice="/m/vo.mp3")
        graph = ";".join(build_audio_filters(spec, _indices(spec)))
        assert "sidechaincompress" in graph
        # The narration feeds both the mix and the compressor's control input.
        assert "asplit=2[speechmix][sidechain]" in graph
        assert "[bed][sidechain]sidechaincompress" in graph
        assert "amix=inputs=2:duration=longest:normalize=0[aout]" in graph

    def test_the_mix_does_not_renormalize_and_halve_the_levels(self):
        spec = _spec(music="/m/bed.mp3", voice="/m/vo.mp3")
        graph = ";".join(build_audio_filters(spec, _indices(spec)))
        assert "normalize=0" in graph

    def test_audio_stream_indices_follow_the_visuals(self):
        spec = _spec(
            visuals=(
                VisualClip.from_path("/m/a.mp4"),
                VisualClip.from_path("/m/b.png", 2),
            ),
            music="/m/bed.mp3",
            voice="/m/vo.mp3",
        )
        indices = _indices(spec)
        assert indices["visual:0"] == 0
        assert indices["visual:1"] == 1
        assert indices["music"] == 2
        assert indices["voice"] == 3


class TestFfmpegArgs:
    def test_stills_are_looped_for_their_duration(self):
        spec = _spec(visuals=(VisualClip.from_path("/m/frame.png", 4),))
        args = build_ffmpeg_args(spec)
        assert "-loop" in args
        loop_at = args.index("-loop")
        assert args[loop_at : loop_at + 5] == ["-loop", "1", "-t", "4", "-i", "/m/frame.png"][:5]
        assert args[loop_at + 4] == "-i"

    def test_video_clips_are_not_looped(self):
        args = build_ffmpeg_args(_spec())
        assert "-loop" not in args

    def test_a_measured_timeline_loops_the_bed_and_caps_the_output(self):
        args = build_ffmpeg_args(_spec(music="/m/bed.mp3"), total_duration=7.5)
        assert args[args.index("-stream_loop") + 1] == "-1"
        assert args[args.index("-t") + 1] == "7.5"
        assert "-shortest" not in args

    def test_an_unmeasured_timeline_never_creates_an_endless_input(self):
        # This is the hang the end-to-end test caught. An endless bed plus an
        # apad'ed narration means no input ever reaches end of stream, so -shortest
        # never fires and ffmpeg renders forever. With no measurement, every input
        # must stay finite.
        args = build_ffmpeg_args(_spec(music="/m/bed.mp3", voice="/m/vo.mp3"))
        assert "-stream_loop" not in args
        assert "-shortest" in args
        graph = args[args.index("-filter_complex") + 1]
        assert "apad" not in graph

    def test_a_measured_timeline_pads_audio_to_span_the_edit(self):
        args = build_ffmpeg_args(
            _spec(music="/m/bed.mp3", voice="/m/vo.mp3"), total_duration=7.5
        )
        graph = args[args.index("-filter_complex") + 1]
        assert "apad" in graph

    def test_a_silent_video_needs_no_bound_at_all(self):
        args = build_ffmpeg_args(_spec())
        assert "-shortest" not in args
        assert "-t" not in args

    def test_narration_is_not_looped(self):
        args = build_ffmpeg_args(_spec(voice="/m/vo.mp3"))
        voice_at = args.index("/m/vo.mp3")
        assert args[voice_at - 1] == "-i"
        assert "-stream_loop" not in args

    def test_a_silent_video_is_explicitly_muxed_without_audio(self):
        args = build_ffmpeg_args(_spec())
        assert "-an" in args
        assert "-c:a" not in args

    def test_an_audio_video_is_encoded_with_aac(self):
        args = build_ffmpeg_args(_spec(music="/m/bed.mp3"))
        assert args[args.index("-c:a") + 1] == "aac"
        assert "-an" not in args

    def test_web_playable_defaults(self):
        args = build_ffmpeg_args(_spec())
        assert args[args.index("-c:v") + 1] == "libx264"
        assert args[args.index("-pix_fmt") + 1] == "yuv420p"
        assert args[args.index("-movflags") + 1] == "+faststart"

    def test_the_output_path_is_last_and_overwrite_is_explicit(self):
        args = build_ffmpeg_args(_spec(output="/m/final.mp4"))
        assert args[-1] == "/m/final.mp4"
        assert "-y" in args

    def test_the_binary_is_configurable(self):
        args = build_ffmpeg_args(_spec(), ffmpeg="/opt/ffmpeg")
        assert args[0] == "/opt/ffmpeg"

    def test_metadata_is_written_deterministically(self):
        spec = _spec(extra_metadata={"title": "Launch", "comment": "navin"})
        args = build_ffmpeg_args(spec)
        assert "comment=navin" in args
        assert "title=Launch" in args
        assert args.index("comment=navin") < args.index("title=Launch")

    def test_an_invalid_spec_fails_before_building_a_command(self):
        with pytest.raises(AssembleError):
            build_ffmpeg_args(_spec(visuals=()))


class TestSpecFromPaths:
    def test_mixed_paths_get_the_right_kinds(self):
        spec = spec_from_paths(
            visuals=["/m/a.mp4", "/m/b.png"], output="/m/out.mp4"
        )
        assert spec.visuals[0].kind == KIND_VIDEO
        assert spec.visuals[1].kind == KIND_IMAGE
        assert spec.visuals[1].duration == DEFAULT_IMAGE_DURATION

    def test_durations_line_up_with_visuals(self):
        spec = spec_from_paths(
            visuals=["/m/a.png", "/m/b.png"],
            durations=[2.0, 4.0],
            output="/m/out.mp4",
        )
        assert [clip.duration for clip in spec.visuals] == [2.0, 4.0]

    def test_a_mismatched_duration_list_is_refused(self):
        with pytest.raises(AssembleError, match="durations has"):
            spec_from_paths(
                visuals=["/m/a.png", "/m/b.png"],
                durations=[2.0],
                output="/m/out.mp4",
            )

    def test_blank_audio_paths_become_none(self):
        spec = spec_from_paths(
            visuals=["/m/a.mp4"], output="/m/out.mp4", music="", voice=""
        )
        assert spec.music is None
        assert spec.voice is None
        assert spec.has_audio is False

    def test_trims_line_up_with_visuals(self):
        spec = spec_from_paths(
            visuals=["/m/a.mp4", "/m/b.mp4"],
            trims=[(2.0, 8.0), (None, None)],
            output="/m/out.mp4",
        )
        assert (spec.visuals[0].start, spec.visuals[0].end) == (2.0, 8.0)
        assert (spec.visuals[1].start, spec.visuals[1].end) == (None, None)

    def test_a_mismatched_trim_list_is_refused(self):
        with pytest.raises(AssembleError, match="trims has"):
            spec_from_paths(
                visuals=["/m/a.mp4", "/m/b.mp4"],
                trims=[(2.0, 8.0)],
                output="/m/out.mp4",
            )

    def test_crossfade_is_the_name_people_type_for_fade(self):
        spec = spec_from_paths(
            visuals=["/m/a.mp4", "/m/b.mp4"],
            output="/m/out.mp4",
            transition="Crossfade",
        )
        assert spec.transition == "fade"
        assert spec.transition_duration == DEFAULT_TRANSITION_DURATION

    def test_no_transition_stays_a_hard_cut(self):
        spec = spec_from_paths(
            visuals=["/m/a.mp4", "/m/b.mp4"], output="/m/out.mp4", transition=""
        )
        assert spec.transition == "none"
        assert spec.has_transitions is False

    def test_the_full_stack_is_wired(self):
        spec = spec_from_paths(
            visuals=["/m/clip.mp4", "/m/still.png"],
            durations=[0, 3],
            music="/m/bed.mp3",
            voice="/m/vo.mp3",
            subtitles="/m/subs.srt",
            output="/m/final.mp4",
            width=1920,
            height=1080,
            fps=24,
        )
        graph = build_filter_complex(spec, _indices(spec))
        assert "concat=n=2" in graph
        assert "sidechaincompress" in graph
        assert "subtitles=" in graph
        assert spec.has_audio is True


class TestTimelineMeasurement:
    def test_stills_alone_need_no_probing(self):
        spec = _spec(
            visuals=(
                VisualClip.from_path("/m/a.png", 2),
                VisualClip.from_path("/m/b.png", 3.5),
            )
        )
        assert resolve_total_duration(spec) == 5.5

    def test_video_clips_are_probed(self):
        spec = _spec(
            visuals=(
                VisualClip.from_path("/m/a.mp4"),
                VisualClip.from_path("/m/b.png", 2),
            )
        )
        assert resolve_total_duration(spec, {"/m/a.mp4": 4.0}.get) == 6.0

    def test_an_unprobeable_clip_gives_up_rather_than_guessing(self):
        # A wrong -t would truncate or pad the deliverable, so bail to -shortest.
        spec = _spec(visuals=(VisualClip.from_path("/m/a.mp4"),))
        assert resolve_total_duration(spec, lambda _p: None) is None
        assert resolve_total_duration(spec, None) is None

    def test_one_unprobeable_clip_invalidates_the_whole_timeline(self):
        spec = _spec(
            visuals=(
                VisualClip.from_path("/m/a.mp4"),
                VisualClip.from_path("/m/b.mp4"),
            )
        )
        assert resolve_total_duration(spec, {"/m/a.mp4": 4.0}.get) is None

    def test_a_zero_probe_is_treated_as_unknown(self):
        spec = _spec(visuals=(VisualClip.from_path("/m/a.mp4"),))
        assert resolve_total_duration(spec, lambda _p: 0.0) is None

    def test_ffprobe_is_taken_from_the_ffmpeg_that_is_actually_used(self, tmp_path):
        sibling = tmp_path / "ffprobe"
        sibling.write_text("#!/bin/sh\n")
        assert find_ffprobe(str(tmp_path / "ffmpeg")) == str(sibling)

    def test_ffprobe_falls_back_to_the_path(self, monkeypatch):
        monkeypatch.setattr("shutil.which", lambda _name: "/usr/bin/ffprobe")
        assert find_ffprobe("/nowhere/ffmpeg") == "/usr/bin/ffprobe"

    @pytest.mark.parametrize(
        ("header", "expected"),
        [
            ("  Duration: 00:00:01.00, start: 0.000000, bitrate: 30 kb/s", 1.0),
            ("Duration: 00:01:30.50, start: 0.0", 90.5),
            ("Duration: 01:00:00.00", 3600.0),
        ],
    )
    def test_a_duration_is_read_from_the_ffmpeg_header(self, header, expected):
        # The montage installer ships ffmpeg without ffprobe, so this parser is the
        # only measurement available on a machine Navin set up itself.
        assert parse_ffmpeg_duration(header) == pytest.approx(expected)

    @pytest.mark.parametrize(
        "text", ["", "no duration here", "Duration: N/A, start: 0.0", "Duration: 00:00:00.00"]
    )
    def test_an_unreadable_header_yields_no_duration(self, text):
        assert parse_ffmpeg_duration(text) is None

    def test_ffmpeg_measures_a_real_clip_without_ffprobe(self, tmp_path):
        from navin.montage.detect import find_ffmpeg

        binary = find_ffmpeg()
        if not binary:
            pytest.skip("ffmpeg not installed")
        clip = tmp_path / "clip.mp4"
        asyncio.run(
            _ffmpeg(
                binary,
                ["-f", "lavfi", "-i", "color=c=green:s=160x120:d=2", "-r", "12", str(clip)],
            )
        )
        measured = asyncio.run(probe_duration_with_ffmpeg(str(clip), binary))
        assert measured == pytest.approx(2.0, abs=0.3)

    def test_measuring_falls_back_to_ffmpeg_when_ffprobe_is_absent(
        self, tmp_path, monkeypatch
    ):
        from navin.montage.detect import find_ffmpeg

        binary = find_ffmpeg()
        if not binary:
            pytest.skip("ffmpeg not installed")
        monkeypatch.setattr("navin.montage.assemble.find_ffprobe", lambda _f=None: None)
        clip = tmp_path / "clip.mp4"
        asyncio.run(
            _ffmpeg(
                binary,
                ["-f", "lavfi", "-i", "color=c=red:s=160x120:d=1", "-r", "12", str(clip)],
            )
        )
        spec = spec_from_paths(
            visuals=[str(clip), str(tmp_path / "x.png")],
            durations=[0, 2],
            output=str(tmp_path / "out.mp4"),
        )
        total = asyncio.run(measure_total_duration(spec, binary))
        assert total == pytest.approx(3.0, abs=0.3)


class TestArgumentParsing:
    def test_paths_are_split_and_trimmed(self):
        assert parse_path_list(" a.mp4 , b.png ,, ") == ["a.mp4", "b.png"]

    def test_empty_input_yields_no_paths(self):
        assert parse_path_list("") == []
        assert parse_path_list(None) == []

    def test_durations_are_numbers(self):
        assert parse_durations("2, 3.5 ,4") == [2.0, 3.5, 4.0]

    def test_a_non_numeric_duration_is_refused_with_the_bad_value(self):
        with pytest.raises(AssembleError, match="'trois'"):
            parse_durations("2, trois")

    def test_trims_cover_every_shape_of_cut(self):
        assert parse_trims("2-8, 3- , -5 , - ,") == [
            (2.0, 8.0),
            (3.0, None),
            (None, 5.0),
            (None, None),
            (None, None),
        ]

    def test_no_trims_means_no_entries(self):
        assert parse_trims("") == []
        assert parse_trims(None) == []
        assert parse_trims("   ") == []

    @pytest.mark.parametrize("bad", ["2..8", "abc-5", "2-huit", "5"])
    def test_a_malformed_trim_is_refused_with_examples(self, bad):
        with pytest.raises(AssembleError, match="start-end"):
            parse_trims(bad)


class TestRunAssemble:
    def test_a_missing_ffmpeg_is_reported_with_the_fix(self, monkeypatch):
        monkeypatch.setattr("navin.montage.detect.find_ffmpeg", lambda: None)
        result = asyncio.run(run_assemble(_spec()))
        assert result["ok"] is False
        assert "action=setup" in str(result["error"])

    def test_a_real_render_produces_a_playable_file(self, tmp_path, monkeypatch):
        from navin.montage.detect import find_ffmpeg

        binary = find_ffmpeg()
        if not binary:
            pytest.skip("ffmpeg not installed")
        # A tiny synthetic still and tone keep this fast but genuinely end to end.
        still = tmp_path / "frame.png"
        tone = tmp_path / "bed.wav"
        asyncio.run(_ffmpeg(binary, ["-f", "lavfi", "-i", "color=c=red:s=320x240:d=1", "-frames:v", "1", str(still)]))
        asyncio.run(_ffmpeg(binary, ["-f", "lavfi", "-i", "sine=frequency=440:duration=1", str(tone)]))

        out = tmp_path / "final.mp4"
        spec = spec_from_paths(
            visuals=[str(still)],
            durations=[1.0],
            music=str(tone),
            output=str(out),
            width=320,
            height=240,
            fps=12,
        )
        result = asyncio.run(run_assemble(spec, timeout_s=120))
        assert result["ok"] is True, result.get("error")
        assert out.is_file()
        assert result["size_bytes"] > 0
        assert result["has_music"] is True
        assert result["has_voice"] is False

    def test_a_real_trim_and_crossfade_render_end_to_end(self, tmp_path):
        from navin.montage.detect import find_ffmpeg

        binary = find_ffmpeg()
        if not binary:
            pytest.skip("ffmpeg not installed")
        first = tmp_path / "a.mp4"
        second = tmp_path / "b.mp4"
        asyncio.run(_ffmpeg(binary, ["-f", "lavfi", "-i", "color=c=red:s=160x120:d=2", "-r", "12", str(first)]))
        asyncio.run(_ffmpeg(binary, ["-f", "lavfi", "-i", "color=c=blue:s=160x120:d=2", "-r", "12", str(second)]))

        out = tmp_path / "final.mp4"
        spec = spec_from_paths(
            visuals=[str(first), str(second)],
            trims=[(0.5, 1.5), (None, None)],
            transition="fade",
            transition_duration=0.3,
            output=str(out),
            width=160,
            height=120,
            fps=12,
        )
        result = asyncio.run(run_assemble(spec, timeout_s=120))
        assert result["ok"] is True, result.get("error")
        assert result["transition"] == "fade"
        # 1s trimmed + 2s full - 0.3s of overlap.
        assert result["duration_s"] == pytest.approx(2.7, abs=0.3)
        assert out.is_file()

    def test_an_unmeasurable_fade_fails_readably_instead_of_rendering_junk(
        self, monkeypatch
    ):
        # No ffprobe and an unreadable clip: the fade offsets cannot be placed.
        monkeypatch.setattr("navin.montage.detect.find_ffmpeg", lambda: "/opt/ffmpeg")
        monkeypatch.setattr("navin.montage.assemble.find_ffprobe", lambda _f=None: None)

        async def _no_measure(_path, _ffmpeg, timeout_s=20.0):
            return None

        monkeypatch.setattr(
            "navin.montage.assemble.probe_duration_with_ffmpeg", _no_measure
        )
        spec = _spec(
            visuals=(
                VisualClip.from_path("/m/a.mp4"),
                VisualClip.from_path("/m/b.mp4"),
            ),
            transition="fade",
        )
        result = asyncio.run(run_assemble(spec))
        assert result["ok"] is False
        assert "transition=none" in str(result["error"])

    def test_a_bad_input_reports_the_ffmpeg_cause_not_the_banner(self, tmp_path):
        from navin.montage.detect import find_ffmpeg

        if not find_ffmpeg():
            pytest.skip("ffmpeg not installed")
        missing = tmp_path / "nope.mp4"
        spec = spec_from_paths(
            visuals=[str(missing)], output=str(tmp_path / "out.mp4")
        )
        result = asyncio.run(run_assemble(spec, timeout_s=60))
        assert result["ok"] is False
        assert "command" in result
        assert len(str(result["error"]).splitlines()) <= 6


class TestMontageToolAction:
    """The action must be reachable and readable from the tool surface."""

    def _tool(self, workspace):
        from navin.agent.tools.context import ToolContext
        from navin.agent.tools.montage import MontageTool
        from navin.config.schema import ToolsConfig

        return MontageTool.create(
            ToolContext(config=ToolsConfig(), workspace=str(workspace))
        )

    def test_assemble_is_offered_in_the_schema(self, tmp_path):
        schema = self._tool(tmp_path).to_schema()
        props = schema["function"]["parameters"]["properties"]
        assert "assemble" in props["action"]["enum"]
        for key in (
            "visuals",
            "durations",
            "trims",
            "transition",
            "transition_duration",
            "music_gain_db",
            "music",
            "voice",
        ):
            assert key in props

    def test_a_malformed_trim_from_the_tool_reads_as_an_assemble_error(
        self, tmp_path
    ):
        still = tmp_path / "frame.png"
        still.write_bytes(b"\x89PNG\r\n\x1a\n")
        out = asyncio.run(
            self._tool(tmp_path).execute(
                action="assemble", visuals="frame.png", trims="quatre"
            )
        )
        assert "assemble error" in out
        assert "start-end" in out

    def test_missing_visuals_explains_the_fix(self, tmp_path):
        out = asyncio.run(self._tool(tmp_path).execute(action="assemble"))
        assert "requires visuals=" in out
        assert "generate_video" in out

    def test_an_input_outside_the_workspace_is_refused(self, tmp_path):
        out = asyncio.run(
            self._tool(tmp_path).execute(
                action="assemble", visuals="/etc/hostname.mp4"
            )
        )
        assert "assemble error" in out

    def test_a_full_stack_render_produces_one_deliverable(self, tmp_path):
        from navin.montage.detect import find_ffmpeg

        binary = find_ffmpeg()
        if not binary:
            pytest.skip("ffmpeg not installed")

        still = tmp_path / "frame.png"
        clip = tmp_path / "clip.mp4"
        bed = tmp_path / "bed.wav"
        vo = tmp_path / "vo.wav"
        asyncio.run(_ffmpeg(binary, ["-f", "lavfi", "-i", "color=c=blue:s=160x120:d=1", "-frames:v", "1", str(still)]))
        asyncio.run(_ffmpeg(binary, ["-f", "lavfi", "-i", "color=c=green:s=160x120:d=1", "-r", "12", str(clip)]))
        asyncio.run(_ffmpeg(binary, ["-f", "lavfi", "-i", "sine=frequency=220:duration=1", str(bed)]))
        asyncio.run(_ffmpeg(binary, ["-f", "lavfi", "-i", "sine=frequency=880:duration=1", str(vo)]))

        # The hard timeout is the point of this test: mixing a looped bed with a
        # narration used to render forever, and a hang is the failure mode to catch.
        out = _run_bounded(
            self._tool(tmp_path).execute(
                action="assemble",
                visuals="clip.mp4,frame.png",
                durations="0,1",
                music="bed.wav",
                voice="vo.wav",
                output="final.mp4",
                width=160,
                height=120,
                fps=12,
            ),
            timeout_s=120,
        )
        import json as _json

        payload = _json.loads(out)
        assert payload["ok"] is True, payload.get("error")
        assert payload["visuals"] == 2
        assert payload["has_music"] is True
        assert payload["has_voice"] is True
        # 1s of clip plus 1s of still: the bed must not stretch the deliverable.
        assert payload["duration_s"] == pytest.approx(2.0, abs=0.3)
        assert (tmp_path / "final.mp4").is_file()
        assert "message tool" in payload["next_step"]

    def test_the_default_export_path_lands_in_the_marketing_kit(self, tmp_path):
        from navin.montage.detect import find_ffmpeg

        binary = find_ffmpeg()
        if not binary:
            pytest.skip("ffmpeg not installed")
        still = tmp_path / "frame.png"
        asyncio.run(_ffmpeg(binary, ["-f", "lavfi", "-i", "color=c=red:s=160x120:d=1", "-frames:v", "1", str(still)]))

        out = asyncio.run(
            self._tool(tmp_path).execute(
                action="assemble",
                visuals="frame.png",
                durations="1",
                width=160,
                height=120,
                fps=12,
            )
        )
        import json as _json

        payload = _json.loads(out)
        assert payload["ok"] is True, payload.get("error")
        assert (tmp_path / "marketing/montage/exports/final.mp4").is_file()


def _run_bounded(coro, timeout_s: float):
    """Run *coro* with a hard ceiling so a hang fails the test instead of the run."""

    async def _guard():
        return await asyncio.wait_for(coro, timeout=timeout_s)

    try:
        return asyncio.run(_guard())
    except TimeoutError:
        pytest.fail(f"assemble did not finish within {timeout_s:.0f}s (render hang)")


async def _ffmpeg(binary: str, args: list[str]) -> None:
    process = await asyncio.create_subprocess_exec(
        binary, "-y", "-hide_banner", "-loglevel", "error", *args,
        stdout=asyncio.subprocess.DEVNULL,
        stderr=asyncio.subprocess.PIPE,
    )
    _out, err = await process.communicate()
    if process.returncode != 0:
        raise AssertionError(err.decode("utf-8", "replace"))
