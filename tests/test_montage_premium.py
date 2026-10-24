# Copyright (c) 2026-present Navinspire IA
# SPDX-License-Identifier: AGPL-3.0-only

"""Premium montage output: audio mastering, captions, grade and encode.

Pure graph/argv builders are asserted directly. The parts whose failure only
shows in the rendered file (loudness, letterbox bars, silent SFX, beat grid)
are measured on real ffmpeg output and skipped when ffmpeg is missing.
"""

from __future__ import annotations

import asyncio
import io
import json
import shutil
import subprocess
import wave
from pathlib import Path

import pytest

from navin.montage.assemble import (
    AssembleError,
    AssembleSpec,
    PremiumAssembleSpec,
    assemble,
    build_ffmpeg_args,
    build_video_filters,
    spec_from_paths,
)
from navin.montage.audio import (
    SFX_KINDS,
    AudioMixError,
    AudioMixOptions,
    SfxCue,
    build_mix_graph,
    detect_beats,
    estimate_tempo,
    generate_sfx,
    measure_loudness,
    mix_audio,
    parse_ebur128_summary,
    parse_loudnorm_json,
    resolve_loudness_target,
    snap_to_beats,
    track_beats,
)
from navin.montage.captions import (
    CaptionError,
    CaptionSegment,
    CaptionWord,
    align_script_words,
    aspect_family,
    ass_color,
    ass_time,
    build_captions,
    caption_style,
    count_syllables,
    estimate_word_timings,
    parse_ass_events,
)
from navin.montage.grade import (
    GradeError,
    GradeOptions,
    encode_args,
    fill_chains,
    grade_filter,
    ken_burns_filter,
)

FFMPEG = shutil.which("ffmpeg")
needs_ffmpeg = pytest.mark.skipif(FFMPEG is None, reason="ffmpeg not installed")

BRAND = {
    "name": "Acme",
    "logo_path": "/brand/logo.png",
    "colors": {
        "primary": "#3355FF",
        "secondary": "#22CC88",
        "accent": "#FF5A1F",
        "background": "#0B0B12",
        "text": "#111111",
    },
    "fonts": {"heading": "DejaVu Sans", "body": "DejaVu Sans"},
}


def _run(coro):
    return asyncio.run(coro)


def _ff(*args: str) -> None:
    subprocess.run([FFMPEG, "-y", "-loglevel", "error", *args], check=True)


def _indices(spec: AssembleSpec) -> dict[str, int]:
    from navin.montage.assemble import _input_args

    return _input_args(spec)[1]


# --------------------------------------------------------------------------
# Grade, fill, Ken Burns, encode
# --------------------------------------------------------------------------


class TestLook:
    def test_the_default_spec_keeps_the_historical_letterbox_and_encode(self):
        spec = spec_from_paths(visuals=["/m/a.mp4"], output="/m/out.mp4")
        assert type(spec) is AssembleSpec
        graph = ";".join(build_video_filters(spec, _indices(spec)))
        assert "pad=1080:1920:(ow-iw)/2:(oh-ih)/2,setsar=1" in graph
        args = build_ffmpeg_args(spec)
        assert args[args.index("-preset") + 1] == "medium"
        assert args[args.index("-crf") + 1] == "20"
        assert "-fps_mode" not in args

    def test_premium_turns_everything_on(self):
        spec = spec_from_paths(
            visuals=["/m/a.png", "/m/b.mp4"],
            output="/m/out.mp4",
            music="/m/bed.mp3",
            quality="premium",
        )
        assert isinstance(spec, PremiumAssembleSpec)
        assert spec.fill == "blur"
        assert spec.ken_burns is True
        assert spec.grade == GradeOptions()
        assert spec.encode == "social"
        assert spec.audio_mix is not None and spec.audio_mix.loudness_target == -14.0

    def test_an_explicit_knob_wins_over_the_premium_preset(self):
        spec = spec_from_paths(
            visuals=["/m/a.png"], output="/m/out.mp4", quality="premium",
            fill="color", brand=BRAND, ken_burns=False, encode="web",
        )
        assert spec.fill == "color"
        assert spec.fill_color == "#0B0B12"
        assert spec.ken_burns is False
        # The web delivery is quieter by convention.
        assert spec.audio_mix.loudness_target == -16.0

    def test_blur_fill_replaces_the_black_pad(self):
        spec = spec_from_paths(visuals=["/m/a.png"], output="/m/out.mp4", fill="blur")
        graph = ";".join(build_video_filters(spec, _indices(spec)))
        assert "pad=" not in graph
        assert "boxblur" in graph
        assert "overlay=(W-w)/2:(H-h)/2" in graph

    def test_color_fill_uses_the_brand_color(self):
        chains = fill_chains("[0:v]", "f0", 1080, 1920, mode="color", color="#0b0b12")
        assert chains == [
            "[0:v]scale=1080:1920:force_original_aspect_ratio=decrease,"
            "pad=1080:1920:(ow-iw)/2:(oh-ih)/2:color=0x0B0B12[f0]"
        ]

    def test_a_bad_fill_color_is_refused(self):
        with pytest.raises(AssembleError, match="fill color"):
            build_ffmpeg_args(
                spec_from_paths(
                    visuals=["/m/a.png"], output="/m/o.mp4", fill="color", fill_color="pinkish"
                )
            )

    def test_ken_burns_zooms_stills_at_a_high_internal_resolution(self):
        spec = spec_from_paths(
            visuals=["/m/a.png", "/m/b.mp4"], output="/m/out.mp4",
            durations=[4.0, 0], ken_burns=True,
        )
        args = build_ffmpeg_args(spec)
        # zoompan emits one frame per input frame, so the still must run at fps.
        loop_at = args.index("-loop")
        assert args[loop_at - 2 : loop_at] == ["-framerate", "30"]
        graph = args[args.index("-filter_complex") + 1]
        assert graph.count("zoompan") == 1  # stills only, never the video clip
        assert "scale=3240:5760:flags=lanczos" in graph
        assert "d=1:s=1080x1920:fps=30" in graph

    def test_ken_burns_alternates_direction(self):
        zoom_in = ken_burns_filter(1080, 1920, 30, 3.0, zoom_in=True)
        zoom_out = ken_burns_filter(1080, 1920, 30, 3.0, zoom_in=False)
        assert "z='1+0.12*" in zoom_in
        assert "z='1.12-0.12*" in zoom_out

    def test_grade_is_applied_before_the_captions(self):
        spec = spec_from_paths(
            visuals=["/m/a.mp4"], output="/m/out.mp4", subtitles="/m/c.ass",
            grade=True, lut="/m/look.cube", fonts_dir="/m/fonts",
        )
        graph = ";".join(build_video_filters(spec, _indices(spec)))
        tail = graph.split("[vcat]")[-1]
        assert tail.index("eq=") < tail.index("lut3d=") < tail.index("subtitles=")
        assert "fontsdir='/m/fonts'" in tail

    def test_grade_values_are_bounded(self):
        assert grade_filter(None) == ""
        with pytest.raises(GradeError):
            grade_filter(GradeOptions(contrast=5))
        with pytest.raises(GradeError):
            grade_filter(GradeOptions(lut_path="/m/look.png"))

    def test_encode_presets(self):
        social = encode_args("social", 30, has_audio=True)
        assert social[social.index("-crf") + 1] == "18"
        assert social[social.index("-preset") + 1] == "slow"
        assert social[social.index("-fps_mode") + 1] == "cfr"
        assert social[social.index("-pix_fmt") + 1] == "yuv420p"
        assert social[social.index("-movflags") + 1] == "+faststart"
        master = encode_args("master", 30, has_audio=False)
        assert master[master.index("-crf") + 1] == "14"
        assert "-c:a" not in master
        web = encode_args("web", 30, has_audio=True)
        assert web[web.index("-crf") + 1] == "22"
        prores = encode_args("master_prores", 25, has_audio=True)
        assert prores[prores.index("-c:v") + 1] == "prores_ks"

    def test_prores_needs_a_mov_container(self):
        spec = spec_from_paths(visuals=["/m/a.mp4"], output="/m/out.mp4", encode="master_prores")
        with pytest.raises(AssembleError, match=".mov"):
            build_ffmpeg_args(spec)

    def test_unknown_quality_is_refused(self):
        with pytest.raises(AssembleError, match="quality"):
            spec_from_paths(visuals=["/m/a.mp4"], output="/m/o.mp4", quality="ultra")


# --------------------------------------------------------------------------
# Audio graph
# --------------------------------------------------------------------------


class TestAudioGraph:
    def test_the_mastered_mix_cleans_ducks_and_fades(self):
        chains = build_mix_graph(
            voice_inputs=[(0, 0.0), (1, 4.25)],
            music_input=2,
            duration=12.0,
            options=AudioMixOptions(sfx=(SfxCue("whoosh", 3.9),)),
        )
        graph = ";".join(chains)
        assert graph.count("highpass=f=80") == 2
        assert "deesser" in graph and "acompressor" in graph
        assert "adelay=delays=4250:all=1" in graph
        assert "sidechaincompress=threshold=0.025:ratio=6:attack=60:release=650" in graph
        assert "afade=t=in:st=0:d=1" in graph
        assert "afade=t=out:st=10.5:d=1.5" in graph
        assert "anoisesrc" in graph and "adelay=delays=3900:all=1" in graph
        assert graph.endswith("[mix]")

    def test_sfx_past_the_end_are_dropped(self):
        chains = build_mix_graph(
            voice_inputs=[(0, 0.0)], music_input=None, duration=5.0,
            options=AudioMixOptions(sfx=(SfxCue("pop", 9.0),)),
        )
        assert "aevalsrc" not in ";".join(chains)

    def test_nothing_to_mix_is_an_error(self):
        with pytest.raises(AudioMixError):
            build_mix_graph(voice_inputs=[], music_input=None, duration=None,
                            options=AudioMixOptions())

    def test_premium_assemble_graph_carries_a_loudnorm(self):
        spec = spec_from_paths(
            visuals=["/m/a.mp4"], output="/m/o.mp4", music="/m/bed.mp3",
            voice="/m/vo.mp3", loudness="web",
        )
        args = build_ffmpeg_args(spec, total_duration=8.0)
        graph = args[args.index("-filter_complex") + 1]
        assert "loudnorm=I=-16:TP=-1" in graph
        assert "[aout]" in graph

    def test_loudness_targets(self):
        assert resolve_loudness_target("social") == -14.0
        assert resolve_loudness_target("web") == -16.0
        assert resolve_loudness_target(-23) == -23.0
        assert resolve_loudness_target(None) is None
        with pytest.raises(AudioMixError):
            resolve_loudness_target("loud")

    def test_loudnorm_json_is_parsed_and_silence_is_rejected(self):
        stderr = (
            "[Parsed_loudnorm_0 @ 0x1]\n{\n\"input_i\" : \"-20.31\",\n\"input_tp\" : \"-4.2\","
            "\n\"input_lra\" : \"3.1\",\n\"input_thresh\" : \"-30.5\",\n\"output_i\" : \"-14\","
            "\n\"target_offset\" : \"0.1\"\n}\n"
        )
        assert parse_loudnorm_json(stderr)["input_i"] == -20.31
        assert parse_loudnorm_json(stderr.replace("-20.31", "-inf")) is None
        assert parse_loudnorm_json("no json") is None

    def test_ebur128_summary_is_parsed(self):
        stderr = (
            "Summary:\n\n  Integrated loudness:\n    I:         -14.1 LUFS\n"
            "  Loudness range:\n    LRA:         5.0 LU\n  True peak:\n    Peak:       -1.3 dBFS\n"
        )
        assert parse_ebur128_summary(stderr) == {
            "integrated": -14.1, "lra": 5.0, "true_peak": -1.3,
        }


class TestBeats:
    def test_cuts_snap_to_the_nearest_beat_within_half_a_beat(self):
        beats = [0.0, 0.5, 1.0, 1.5, 2.0, 2.5, 3.0]
        assert snap_to_beats([0.93, 2.2, 2.9], beats) == [1.0, 2.0, 3.0]

    def test_a_far_cut_is_left_alone(self):
        assert snap_to_beats([5.0], [0.0, 1.0, 2.0]) == [5.0]

    def test_cuts_never_collapse_onto_one_beat(self):
        out = snap_to_beats([2.3, 2.4], [2.0, 2.5, 3.0], min_gap=0.4)
        assert out[0] == 2.5
        assert out[1] > out[0]

    def test_no_beats_means_no_change(self):
        assert snap_to_beats([1.2, 3.4], []) == [1.2, 3.4]

    def test_tempo_and_phase_from_a_synthetic_envelope(self):
        frame_rate = 100.0
        envelope = [0.0] * 1000
        for frame in range(10, 1000, 50):  # 120 BPM, first beat at 0.1 s
            envelope[frame] = 1.0
        tempo = estimate_tempo(envelope, frame_rate)
        assert tempo == pytest.approx(120.0, abs=1.0)
        beats = track_beats(envelope, frame_rate, tempo)
        assert beats[0] == pytest.approx(0.1, abs=0.02)
        assert beats[1] - beats[0] == pytest.approx(0.5, abs=0.02)


# --------------------------------------------------------------------------
# Captions
# --------------------------------------------------------------------------


class TestCaptions:
    def test_syllables_weight_the_estimate(self):
        assert count_syllables("ship") == 1
        assert count_syllables("marketing") == 3
        assert count_syllables("2026") == 4
        words = estimate_word_timings("Meet Acme, the fastest way.", 1.0, 4.0)
        assert words[0].start == 1.0 and words[-1].end == 4.0
        assert all(a.end <= b.start + 1e-9 for a, b in zip(words, words[1:], strict=False))
        # The comma after "Acme," leaves a pause before "the".
        assert words[2].start > words[1].end

    def test_caption_file_timing_matches_the_segments(self, tmp_path):
        segments = [
            {"text": "Meet Acme, the fastest way to ship.", "start": 0.5, "end": 3.2},
            {"text": "Try it free today!", "start": 3.6, "end": 5.8},
        ]
        result = build_captions(segments, tmp_path / "c.ass", width=1080, height=1920)
        events = parse_ass_events(Path(result.path).read_text(encoding="utf-8"))
        assert result.events == len(events)
        # One event per spoken word in highlight mode.
        assert len(events) == 7 + 4
        first, last = events[:7], events[7:]
        assert first[0][0] == pytest.approx(0.5) and first[-1][1] == pytest.approx(3.2)
        assert last[0][0] == pytest.approx(3.6) and last[-1][1] == pytest.approx(5.8)
        for start, end, _text in events:
            assert end > start
            assert 0.5 <= start and end <= 5.8
            assert not (3.2 < start < 3.6)

    def test_provider_word_timings_are_used_and_clamped(self, tmp_path):
        segment = CaptionSegment(
            "Hello world", 2.0, 3.0,
            (CaptionWord("Hello", 2.1, 2.4), CaptionWord("world", 2.5, 3.6)),
        )
        result = build_captions([segment], tmp_path / "c.ass", width=1920, height=1080)
        events = parse_ass_events(Path(result.path).read_text(encoding="utf-8"))
        assert events[0][0] == pytest.approx(2.0)
        assert events[1][0] == pytest.approx(2.5)
        assert events[-1][1] == pytest.approx(3.0)

    def test_relative_words_from_the_speech_tool_are_offset(self, tmp_path):
        result = build_captions(
            [{"text": "Go now", "start": 10.0, "duration_s": 1.0, "words_relative": True,
              "words": [{"text": "Go", "start": 0.0, "end": 0.4},
                        {"text": "now", "start": 0.5, "end": 1.0}]}],
            tmp_path / "c.ass", width=1080, height=1080,
        )
        assert result.segments[0]["words"][1]["start"] == pytest.approx(10.5)

    def test_the_current_word_is_highlighted_in_the_brand_color(self, tmp_path):
        result = build_captions(
            [{"text": "Ship faster", "start": 0, "end": 2}], tmp_path / "c.ass", brand=BRAND,
        )
        text = Path(result.path).read_text(encoding="utf-8")
        accent = ass_color("#FF5A1F")
        assert f"\\c{accent}" in text
        # Dark brand text would vanish over footage, so the fill stays light.
        assert "Style: Caption,DejaVu Sans," in text
        assert ass_color("#FFFFFF") in text.split("Style: Caption,")[1].split(",")[2]

    def test_karaoke_mode_sweeps_each_word(self, tmp_path):
        result = build_captions(
            [{"text": "One two three", "start": 0, "end": 3}], tmp_path / "c.ass",
            mode="karaoke",
        )
        events = parse_ass_events(Path(result.path).read_text(encoding="utf-8"))
        assert len(events) == 1
        assert events[0][2].count("\\kf") == 3

    @pytest.mark.parametrize(
        ("size", "family", "bottom_fraction"),
        [((1080, 1920), "vertical", 0.24), ((1920, 1080), "wide", 0.08),
         ((1080, 1080), "square", 0.12)],
    )
    def test_margins_follow_the_platform_safe_area(self, size, family, bottom_fraction):
        width, height = size
        assert aspect_family(width, height) == family
        style = caption_style(width, height)
        assert style.margin_v == round(height * bottom_fraction)
        assert style.margin_l == style.margin_r > 0

    def test_brand_fonts_are_staged_for_libass(self, tmp_path):
        font = tmp_path / "Brand-Bold.ttf"
        font.write_bytes(b"\0\1\0\0fake")
        brand = dict(BRAND, font_files=[str(font)])
        result = build_captions(
            [{"text": "Hi", "start": 0, "end": 1}], tmp_path / "out" / "c.ass", brand=brand
        )
        assert result.fonts_dir is not None
        assert (Path(result.fonts_dir) / "Brand-Bold.ttf").is_file()

    def test_forced_alignment_keeps_the_script_text(self):
        heard = [
            {"text": "meet", "start": 0.1, "end": 0.4},
            {"text": "acne", "start": 0.45, "end": 0.9},  # misheard brand name
            {"text": "today", "start": 1.2, "end": 1.7},
        ]
        words = align_script_words("Meet Acme today.", heard, 5.0, 7.0)
        assert [w.text for w in words] == ["Meet", "Acme", "today."]
        assert words[0].start == pytest.approx(5.1)
        assert words[2].start == pytest.approx(6.2)
        assert words[0].end <= words[1].start <= words[1].end <= words[2].start

    def test_bad_segments_are_refused(self, tmp_path):
        with pytest.raises(CaptionError):
            build_captions([], tmp_path / "c.ass")
        with pytest.raises(CaptionError):
            build_captions([{"text": "x", "start": 2, "end": 1}], tmp_path / "c.ass")

    def test_ass_time(self):
        assert ass_time(3723.456) == "1:02:03.46"


# --------------------------------------------------------------------------
# TTS timestamp parsers
# --------------------------------------------------------------------------


class TestTtsTimestamps:
    @pytest.fixture(autouse=True)
    def _load_audio_package_first(self):
        # navin.providers.tts and navin.audio import each other; the app
        # always enters through navin.audio, so the tests do too.
        import navin.audio.tts  # noqa: F401

    def test_elevenlabs_alignment_groups_characters_into_words(self):
        from navin.providers.tts import words_from_elevenlabs_alignment

        alignment = {
            "characters": list("Hi you"),
            "character_start_times_seconds": [0.0, 0.1, 0.2, 0.3, 0.4, 0.5],
            "character_end_times_seconds": [0.1, 0.2, 0.3, 0.4, 0.5, 0.6],
        }
        assert words_from_elevenlabs_alignment(alignment) == [
            {"text": "Hi", "start": 0.0, "end": 0.2},
            {"text": "you", "start": 0.3, "end": 0.6},
        ]

    def test_verbose_transcription_words(self):
        from navin.providers.tts import words_from_verbose_transcription

        payload = {"segments": [{"words": [{"word": " Hello", "start": 0.0, "end": 0.5}]}]}
        assert words_from_verbose_transcription(payload) == [
            {"text": "Hello", "start": 0.0, "end": 0.5}
        ]
        assert words_from_verbose_transcription({"words": [{"word": "x"}]}) == []


# --------------------------------------------------------------------------
# Segmented narration
# --------------------------------------------------------------------------


def _wav(seconds: float, rate: int = 16000) -> bytes:
    buffer = io.BytesIO()
    with wave.open(buffer, "wb") as out:
        out.setnchannels(1)
        out.setsampwidth(2)
        out.setframerate(rate)
        out.writeframes(b"\0\0" * int(seconds * rate))
    return buffer.getvalue()


def _tts(provider: str = "navin"):
    from navin.audio.tts import EffectiveTtsConfig

    return EffectiveTtsConfig(
        provider=provider, model="tts-1", voice="alloy", auto_speak=False,
        api_key="sk-test", api_base="https://api.example.test/v1", response_format="wav",
    )


class TestSegmentedSpeech:
    def _tool(self, **config):
        from navin.agent.tools.speech_generation import (
            SpeechGenerationTool,
            SpeechGenerationToolConfig,
        )

        return SpeechGenerationTool(workspace="/tmp", config=SpeechGenerationToolConfig(**config))

    def test_one_clip_per_segment_with_durations_and_words(self, monkeypatch):
        lengths = {"Scene one is here.": 1.5, "And scene two.": 2.25}
        seen: list[tuple[str, str, str]] = []

        async def fake_synth(text, config):
            seen.append((text, config.voice, config.model))
            return _wav(lengths[text])

        monkeypatch.setattr("navin.audio.tts.resolve_tts_config", lambda _c: _tts())
        monkeypatch.setattr("navin.audio.tts.synthesize_speech_with_config", fake_synth)
        out = _run(self._tool(narration_voice="onyx").execute(segments=list(lengths)))
        payload = json.loads(str(out))
        rows = payload["segments"]
        assert [row["duration_s"] for row in rows] == [1.5, 2.25]
        assert payload["total_duration_s"] == 3.75
        assert all(row["duration_measured"] for row in rows)
        assert rows[0]["timing_source"] == "estimate"
        assert rows[0]["words_relative"] is True
        assert rows[0]["words"][0]["start"] == 0.0
        assert rows[1]["words"][-1]["end"] == pytest.approx(2.25)
        assert len(payload["artifacts"]) == 2
        # Narration uses its own voice and the higher-fidelity model.
        assert {(voice, model) for _t, voice, model in seen} == {("onyx", "tts-1-hd")}

    def test_openai_word_timestamps_drive_the_alignment(self, monkeypatch):
        async def fake_synth(text, config):
            return _wav(2.0)

        async def fake_words(audio, **kwargs):
            assert kwargs["model"] == "whisper-1"
            return [{"text": "hello", "start": 0.2, "end": 0.6},
                    {"text": "there", "start": 0.9, "end": 1.6}]

        monkeypatch.setattr("navin.audio.tts.resolve_tts_config", lambda _c: _tts("openai"))
        monkeypatch.setattr("navin.audio.tts.synthesize_speech_with_config", fake_synth)
        monkeypatch.setattr("navin.providers.tts.transcribe_word_timestamps", fake_words)
        payload = json.loads(str(_run(self._tool().execute(segments=["Hello there."]))))
        row = payload["segments"][0]
        assert row["timing_source"] == "alignment"
        assert row["words"][0] == {"text": "Hello", "start": 0.2, "end": 0.6}
        assert row["words"][1]["text"] == "there."

    def test_segments_are_validated(self, monkeypatch):
        monkeypatch.setattr("navin.audio.tts.resolve_tts_config", lambda _c: _tts())
        out = str(_run(self._tool().execute(segments=["ok", "  "])))
        assert "segment 2 is empty" in out

    def test_single_text_mode_is_unchanged(self, monkeypatch):
        seen = []

        async def fake_synth(text, config):
            seen.append(config.model)
            return b"ID3x"

        monkeypatch.setattr("navin.audio.tts.resolve_tts_config", lambda _c: _tts())
        monkeypatch.setattr("navin.audio.tts.synthesize_speech_with_config", fake_synth)
        payload = json.loads(str(_run(self._tool(narration_model="hd").execute(text="salut"))))
        assert seen == ["tts-1"]
        assert "segments" not in payload


# --------------------------------------------------------------------------
# Rendered output (real ffmpeg)
# --------------------------------------------------------------------------


@pytest.fixture
def media(tmp_path):
    if FFMPEG is None:
        pytest.skip("ffmpeg not installed")
    _ff("-f", "lavfi", "-i", "testsrc2=s=640x360:d=1", "-frames:v", "1",
        str(tmp_path / "wide.png"))
    _ff("-f", "lavfi", "-i", "testsrc2=s=640x360:d=3", "-c:v", "libx264",
        "-pix_fmt", "yuv420p", str(tmp_path / "clip.mp4"))
    # Speech-like: a pitched tone with a syllable-rate tremolo.
    _ff("-f", "lavfi", "-i",
        "aevalsrc='0.25*sin(2*PI*220*t)*(0.6+0.4*sin(2*PI*4*t))':s=24000:d=2.5",
        str(tmp_path / "vo.wav"))
    # A 120 BPM kick pattern with a quiet pad.
    _ff("-f", "lavfi", "-i",
        "aevalsrc='0.8*sin(2*PI*60*t)*exp(-mod(t,0.5)*18)+0.05*sin(2*PI*440*t)':s=44100:d=10",
        str(tmp_path / "music.wav"))
    return tmp_path


def _top_band_luma(video: Path, at: float, width: int, height: int) -> float:
    raw = subprocess.run(
        [FFMPEG, "-loglevel", "error", "-ss", str(at), "-i", str(video), "-frames:v", "1",
         "-f", "rawvideo", "-pix_fmt", "gray", "-"],
        check=True, capture_output=True,
    ).stdout
    band = raw[: width * (height // 10)]
    return sum(band) / len(band)


@needs_ffmpeg
class TestRendered:
    @pytest.mark.parametrize("kind", SFX_KINDS)
    def test_every_procedural_sfx_is_audible(self, tmp_path, kind):
        result = _run(generate_sfx(kind, tmp_path / f"{kind}.wav"))
        measured = _run(measure_loudness(result["path"]))
        assert measured["true_peak"] is not None
        assert measured["true_peak"] > -20.0, measured

    @pytest.mark.parametrize(("target", "loudness"), [(-14.0, "social"), (-16.0, "web")])
    def test_mix_lands_on_the_loudness_target(self, media, target, loudness):
        result = _run(
            mix_audio(
                media / "mix.wav",
                voice=[(str(media / "vo.wav"), 0.5), (str(media / "vo.wav"), 4.0)],
                music=str(media / "music.wav"),
                sfx=[{"kind": "whoosh", "at": 3.5}],
                duration=8.0,
                loudness=loudness,
            )
        )
        assert result["passes"] == 2
        independent = _run(measure_loudness(result["output"]))
        assert independent["integrated"] == pytest.approx(target, abs=1.0)
        assert independent["true_peak"] <= -1.0 + 0.5

    def test_beats_are_found_on_a_steady_track(self, media):
        grid = _run(detect_beats(media / "music.wav"))
        assert grid["tempo_bpm"] == pytest.approx(120.0, abs=3.0)
        assert len(grid["beats"]) >= 15
        snapped = snap_to_beats([1.1, 3.3], grid["beats"])
        for cut in snapped:
            assert min(abs(cut - beat) for beat in grid["beats"]) < 1e-6

    def test_fill_mode_leaves_no_letterbox_bars(self, media):
        def render(fill: str) -> Path:
            output = media / f"{fill}.mp4"
            spec = spec_from_paths(
                visuals=[str(media / "wide.png")], output=str(output), durations=[1.0],
                width=360, height=640, fill=fill,
            )
            from navin.montage.assemble import run_assemble

            result = _run(run_assemble(spec))
            assert result["ok"], result
            return output

        assert _top_band_luma(render("pad"), 0.5, 360, 640) < 20  # control: black bars
        assert _top_band_luma(render("blur"), 0.5, 360, 640) > 40

    def test_premium_render_is_loud_captioned_and_full_frame(self, media):
        output = media / "premium.mp4"
        result = _run(
            assemble(
                [str(media / "wide.png"), str(media / "clip.mp4")],
                str(output),
                durations=[3.0, 3.0],
                width=360,
                height=640,
                quality="premium",
                music=str(media / "music.wav"),
                voice_segments=[
                    {"path": str(media / "vo.wav"), "at": 0.2,
                     "text": "Meet Acme, the fastest way to ship.", "duration_s": 2.5},
                    {"path": str(media / "vo.wav"), "at": 3.2,
                     "text": "Try it free today!", "duration_s": 2.5},
                ],
                captions=True,
                brand=BRAND,
                sfx=[{"kind": "whoosh", "at": 2.8}],
                transition="fade",
                transition_duration=0.4,
            )
        )
        assert result["ok"], result
        assert result["encode"] == "social" and result["fill"] == "blur"
        assert result["captions"]["events"] == 11
        measured = _run(measure_loudness(output))
        assert measured["integrated"] == pytest.approx(-14.0, abs=1.0)
        assert _top_band_luma(output, 1.0, 360, 640) > 40
        probe = subprocess.run(
            [FFMPEG, "-hide_banner", "-i", str(output)], capture_output=True, text=True
        ).stderr
        assert "yuv420p" in probe and "48000 Hz" in probe and "30 fps" in probe
        # The temporary mastered mix never outlives the render.
        assert not list(media.glob(".premium.mix-*.wav"))
