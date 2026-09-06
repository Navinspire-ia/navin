"""Video localization: silence parsing, segment inversion and SRT output.

The timing math is what makes or breaks a dub: a cue shifted by one silence
window desynchronizes every subtitle after it, and an unsplit two-minute
speech run exceeds what STT providers accept. Those cases are asserted here;
ffmpeg and the STT provider themselves are exercised end to end by the tool.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from navin.montage.localize import (
    _MAX_SEGMENT_S,
    Cue,
    LocalizeError,
    assert_sync_gate_ok,
    atempo_chain,
    build_srt,
    build_sync_report,
    build_voice_filter,
    parse_silences,
    parse_srt,
    plan_placements,
    speech_segments,
    split_cue_text,
)


def _write_sync(audio: Path, *, passed: bool) -> None:
    report = {"gate": {"passed": passed, "threshold_ms": 120}}
    audio.with_suffix(audio.suffix + ".sync.json").write_text(
        json.dumps(report), encoding="utf-8"
    )


def test_sync_gate_blocks_a_failed_voice_track(tmp_path: Path) -> None:
    voice = tmp_path / "voice.wav"
    voice.write_bytes(b"RIFF....")
    _write_sync(voice, passed=False)
    with pytest.raises(LocalizeError, match="sync threshold"):
        assert_sync_gate_ok(voice)


def test_sync_gate_allows_a_passing_or_reportless_track(tmp_path: Path) -> None:
    voice = tmp_path / "voice.wav"
    voice.write_bytes(b"RIFF....")
    assert_sync_gate_ok(voice)  # no report -> allowed (hand-supplied WAV)
    _write_sync(voice, passed=True)
    assert_sync_gate_ok(voice)  # report passed -> allowed

_STDERR = """
[silencedetect @ 0x1] silence_start: 4.2
[silencedetect @ 0x1] silence_end: 5.0 | silence_duration: 0.8
[silencedetect @ 0x1] silence_start: 11.5
[silencedetect @ 0x1] silence_end: 12.4 | silence_duration: 0.9
"""


class TestParseSilences:
    def test_pairs_in_order(self) -> None:
        assert parse_silences(_STDERR) == [(4.2, 5.0), (11.5, 12.4)]

    def test_trailing_open_silence_runs_to_infinity(self) -> None:
        stderr = "[silencedetect] silence_start: 30.0\n"
        pairs = parse_silences(stderr)
        assert pairs[0][0] == 30.0
        assert pairs[0][1] == float("inf")

    def test_no_silence(self) -> None:
        assert parse_silences("frame=1 fps=0") == []


class TestSpeechSegments:
    def test_inverts_silences(self) -> None:
        segments = speech_segments([(4.2, 5.0), (11.5, 12.4)], duration=15.0)
        assert segments == [(0.0, 4.2), (5.0, 11.5), (12.4, 15.0)]

    def test_whole_file_when_no_silence(self) -> None:
        assert speech_segments([], duration=8.0) == [(0.0, 8.0)]

    def test_long_runs_are_split_below_provider_limit(self) -> None:
        segments = speech_segments([], duration=60.0)
        assert len(segments) > 1
        assert all(end - start <= _MAX_SEGMENT_S + 1e-6 for start, end in segments)
        # Splits must tile the run exactly: no gap, no overlap.
        assert segments[0][0] == 0.0
        assert segments[-1][1] == 60.0
        for (_s1, e1), (s2, _e2) in zip(segments, segments[1:]):
            assert abs(e1 - s2) < 1e-6

    def test_sub_cue_noise_is_dropped(self) -> None:
        # 0.1s of "speech" between two silences is a breath, not a cue.
        segments = speech_segments([(0.0, 3.0), (3.1, 6.0)], duration=6.0)
        assert segments == []


class TestBuildSrt:
    def test_timestamps_and_numbering(self) -> None:
        srt = build_srt([
            (0.0, 4.2, "Hello there"),
            (5.0, 11.5, "General Kenobi"),
        ])
        blocks = srt.strip().split("\n\n")
        assert blocks[0] == "1\n00:00:00,000 --> 00:00:04,200\nHello there"
        assert blocks[1] == "2\n00:00:05,000 --> 00:00:11,500\nGeneral Kenobi"

    def test_hour_rollover(self) -> None:
        srt = build_srt([(3661.25, 3662.0, "late cue")])
        assert "01:01:01,250 --> 01:01:02,000" in srt


class TestParseSrt:
    def test_reads_cues_written_by_build_srt(self) -> None:
        srt = build_srt([(0.0, 4.2, "Hello there"), (5.0, 11.5, "General Kenobi")])
        cues = parse_srt(srt)
        assert [(c.start_ms, c.end_ms, c.text) for c in cues] == [
            (0, 4200, "Hello there"),
            (5000, 11500, "General Kenobi"),
        ]

    def test_multi_line_cue_is_joined(self) -> None:
        cues = parse_srt("1\n00:00:01,000 --> 00:00:03,000\nfirst line\nsecond line\n")
        assert cues[0].text == "first line second line"

    def test_renumbers_when_the_translator_broke_the_indices(self) -> None:
        # Translated files come back with duplicated or missing numbers; the
        # timeline is what matters, so indices are rebuilt from the order.
        srt = (
            "7\n00:00:01,000 --> 00:00:02,000\nun\n\n"
            "7\n00:00:03,000 --> 00:00:04,000\ndeux\n"
        )
        assert [cue.index for cue in parse_srt(srt)] == [1, 2]

    def test_accepts_a_dot_as_millisecond_separator(self) -> None:
        cues = parse_srt("1\n00:00:01.500 --> 00:00:02.250\nsalut\n")
        assert (cues[0].start_ms, cues[0].end_ms) == (1500, 2250)

    def test_empty_and_malformed_blocks_are_skipped(self) -> None:
        srt = (
            "1\nnot a timestamp\ntext\n\n"
            "2\n00:00:05,000 --> 00:00:06,000\n\n\n"
            "3\n00:00:07,000 --> 00:00:08,000\nkept\n"
        )
        assert [cue.text for cue in parse_srt(srt)] == ["kept"]

    def test_reversed_timing_is_rejected(self) -> None:
        assert parse_srt("1\n00:00:09,000 --> 00:00:02,000\nnope\n") == []


class TestAtempoChain:
    def test_identity_needs_no_filter(self) -> None:
        assert atempo_chain(1.0) == []

    def test_single_step_inside_the_supported_range(self) -> None:
        assert atempo_chain(1.25) == ["atempo=1.25"]

    def test_large_ratios_are_chained_because_atempo_caps_at_two(self) -> None:
        steps = atempo_chain(3.0)
        assert steps[0] == "atempo=2.0"
        product = 1.0
        for step in steps:
            product *= float(step.split("=")[1])
        assert product == pytest.approx(3.0, rel=1e-3)

    def test_slowdowns_below_the_floor_are_chained_too(self) -> None:
        steps = atempo_chain(0.25)
        product = 1.0
        for step in steps:
            product *= float(step.split("=")[1])
        assert product == pytest.approx(0.25, rel=1e-3)

    def test_zero_is_rejected(self) -> None:
        with pytest.raises(ValueError):
            atempo_chain(0.0)


def _cues(*windows: tuple[int, int]) -> list[Cue]:
    return [
        Cue(index=i + 1, start_ms=start, end_ms=end, text=f"cue {i + 1}")
        for i, (start, end) in enumerate(windows)
    ]


class TestPlanPlacements:
    def test_every_clip_keeps_the_start_time_of_its_cue(self) -> None:
        # This is the whole point: the dub can no longer drift, because each
        # line is pinned to where it was said rather than queued after the
        # previous one.
        cues = _cues((0, 2000), (5000, 7000), (11000, 13000))
        placements = plan_placements(cues, [1800, 1900, 1700])
        assert [p.start_ms for p in placements] == [0, 5000, 11000]
        assert all(p.tempo == 1.0 for p in placements)

    def test_a_clip_that_fits_in_the_following_gap_is_not_sped_up(self) -> None:
        # The cue lasts 2s but the next one starts at 8s: the silence after a
        # sentence is free real estate, so a 5s translation needs no speed-up.
        placements = plan_placements(_cues((0, 2000), (8000, 9000)), [5000, 500])
        assert placements[0].tempo == 1.0
        assert placements[0].overruns is False

    def test_an_overlong_clip_is_sped_up_to_fit_the_gap(self) -> None:
        placements = plan_placements(_cues((0, 2000), (4000, 5000)), [5000, 500])
        # Budget is 4000ms minus the 60ms tail, so the ratio lands near 1.27.
        assert 1.0 < placements[0].tempo <= 1.35
        assert placements[0].played_ms <= 3940
        assert placements[0].overruns is False

    def test_speed_up_is_capped_and_the_overrun_is_reported(self) -> None:
        # Tripling the speed would sound like a chipmunk, so the cap holds and
        # the caller is told which cue spills over instead.
        placements = plan_placements(_cues((0, 1000), (2000, 3000)), [6000, 500])
        assert placements[0].tempo == pytest.approx(1.35)
        assert placements[0].overruns is True

    def test_the_last_cue_may_run_past_its_window(self) -> None:
        # Nothing follows it, so there is nothing to collide with.
        placements = plan_placements(_cues((0, 1000)), [9000])
        assert placements[0].tempo == 1.0
        assert placements[0].overruns is False

    def test_silent_clips_are_dropped(self) -> None:
        placements = plan_placements(_cues((0, 1000), (2000, 3000)), [0, 800])
        assert [p.cue_index for p in placements] == [2]

    def test_mismatched_input_lengths_are_rejected(self) -> None:
        with pytest.raises(ValueError):
            plan_placements(_cues((0, 1000)), [500, 500])

    def test_a_tempo_cap_below_one_is_rejected(self) -> None:
        with pytest.raises(ValueError):
            plan_placements(_cues((0, 1000)), [500], max_tempo=0.9)


class TestBuildVoiceFilter:
    def test_each_clip_is_delayed_to_its_own_timecode(self) -> None:
        placements = plan_placements(_cues((0, 2000), (5000, 7000)), [1800, 1900])
        graph = build_voice_filter(placements)
        assert "adelay=0:all=1" in graph
        assert "adelay=5000:all=1" in graph

    def test_mixing_does_not_normalize_so_volume_survives(self) -> None:
        # amix divides by the input count by default, which would make a
        # forty-cue track inaudible.
        placements = plan_placements(_cues((0, 2000), (5000, 7000)), [1800, 1900])
        graph = build_voice_filter(placements)
        assert "amix=inputs=2:duration=longest:normalize=0[out]" in graph

    def test_a_sped_up_clip_carries_its_atempo(self) -> None:
        placements = plan_placements(_cues((0, 1000), (2000, 3000)), [5000, 500])
        graph = build_voice_filter(placements)
        assert "atempo=" in graph.split(";")[0]

    def test_a_single_clip_skips_the_mixer(self) -> None:
        graph = build_voice_filter(plan_placements(_cues((0, 2000)), [1500]))
        assert "amix" not in graph
        assert graph.endswith("[out]")

    def test_nothing_to_place_is_an_error(self) -> None:
        with pytest.raises(ValueError):
            build_voice_filter([])


def test_sync_report_blocks_over_threshold() -> None:
    placements = plan_placements(
        _cues((0, 1000), (2000, 3000)),
        [6000, 500],
        max_tempo=1.35,
    )
    report = build_sync_report(placements, max_tempo=1.35, threshold_ms=120)
    assert report["drift_ms"] > 120
    assert report["overlaps"]
    assert report["overruns"]
    assert report["gate"] == {
        "passed": False,
        "reason": "sync_threshold_exceeded",
    }


def test_sync_report_passes_clean_timeline() -> None:
    placements = plan_placements(_cues((0, 1000), (2000, 3000)), [800, 500])
    report = build_sync_report(placements, max_tempo=1.35)
    assert report["drift_ms"] == 0
    assert report["gate"]["passed"] is True


def test_long_cue_chunks_without_losing_text() -> None:
    source = "One sentence with several words. " * 30
    chunks = split_cue_text(source, max_chars=80)
    assert len(chunks) > 1
    assert all(len(chunk) <= 80 for chunk in chunks)
    assert " ".join(chunks) == " ".join(source.split())
