# Copyright (c) 2026-present Navinspire IA
# SPDX-License-Identifier: AGPL-3.0-only

from __future__ import annotations

import asyncio
import json
import sys
from pathlib import Path

import pytest

from navin.montage.ffmpeg_runner import (
    redact_argv,
    run_process,
    run_process_sync,
    useful_stderr,
)
from navin.montage.jobs import (
    create_job,
    create_lipsync_job,
    get_job,
    list_jobs,
    run_job,
    run_lipsync_step,
)


def test_lipsync_refuses_an_audio_that_failed_the_sync_gate(tmp_path: Path) -> None:
    from navin.montage.localize import LocalizeError

    audio = tmp_path / "voice.wav"
    audio.write_bytes(b"RIFF....")
    audio.with_suffix(audio.suffix + ".sync.json").write_text(
        json.dumps({"gate": {"passed": False}}), encoding="utf-8"
    )
    payload = {
        "root": str(tmp_path),
        "video": str(tmp_path / "clip.mp4"),
        "audio": str(audio),
        "output": str(tmp_path / "out.mp4"),
        "model": None,
    }
    with pytest.raises(LocalizeError, match="sync threshold"):
        asyncio.run(run_lipsync_step(payload, {}))


def test_runner_returns_structured_success() -> None:
    result = run_process_sync([sys.executable, "-c", "print('ready')"])
    assert result.ok
    assert result.exit_code == 0
    assert result.stdout.strip() == "ready"
    assert result.duration_s >= 0
    assert result.to_dict()["argv"][0] == sys.executable


def test_runner_redacts_flags_urls_and_assignments() -> None:
    safe = redact_argv(
        [
            "ffmpeg",
            "-headers",
            "Authorization: Bearer private",
            "https://u:p@example.test/a?token=secret&part=2",
            "api_key=hidden",
        ]
    )
    rendered = " ".join(safe)
    assert "private" not in rendered
    assert "secret" not in rendered
    assert "hidden" not in rendered
    assert rendered.count("<redacted>") >= 3


def test_runner_timeout_is_typed() -> None:
    result = asyncio.run(
        run_process(
            [sys.executable, "-c", "import time; time.sleep(2)"],
            timeout_s=0.05,
        )
    )
    assert not result.ok
    assert result.timed_out
    assert result.error is not None
    assert result.error.kind == "timeout"


def test_useful_stderr_keeps_actionable_tail() -> None:
    text = "\n".join(["ffmpeg version x", "configuration: noisy", "Invalid data found"])
    assert useful_stderr(text, max_lines=2) == "configuration: noisy\nInvalid data found"


def test_useful_stderr_drops_per_stream_metadata_so_the_cause_is_visible() -> None:
    # A five-clip edit prints a stream header per input; the "No such file"
    # line used to sit below twelve lines of handler_name / vendor_id noise.
    text = "\n".join(
        [
            "Input #3, mov,mp4,m4a,3gp,3g2,mj2, from 'd.mp4':",
            "  Metadata:",
            "    major_brand     : isom",
            "  Stream #3:0[0x1](und): Video: h264 (High), yuv420p, 1080x1920, 30 fps",
            "    Metadata:",
            "      handler_name    : VideoHandler",
            "      vendor_id       : [0][0][0][0]",
            "      encoder         : Lavc61.3.100 libx264",
            "  Stream #3:1[0x2](und): Audio: aac (LC), 44100 Hz, stereo, fltp",
            "      handler_name    : SoundHandler",
            "[in#4 @ 0x31522b40] Error opening input: No such file or directory",
            "Error opening input file /m/exports/verif-demo.mp4.",
            "Error opening input files: No such file or directory",
        ]
    )
    assert useful_stderr(text) == "\n".join(
        [
            "[in#4 @ 0x31522b40] Error opening input: No such file or directory",
            "Error opening input file /m/exports/verif-demo.mp4.",
            "Error opening input files: No such file or directory",
        ]
    )


def test_job_manifest_is_atomic_and_lists(tmp_path: Path) -> None:
    created = create_job(tmp_path, "example", ["download", "render"], payload={"x": 1})
    loaded = get_job(tmp_path, created["id"])
    assert loaded["payload"] == {"x": 1}
    assert list_jobs(tmp_path)[0]["id"] == created["id"]
    folder = tmp_path / "marketing" / "montage" / "jobs"
    assert not list(folder.glob("*.tmp"))
    json.loads((folder / f"{created['id']}.json").read_text(encoding="utf-8"))


def test_job_resumes_failed_step_and_skips_completed_steps(tmp_path: Path) -> None:
    job = create_job(tmp_path, "example", ["first", "second"])
    calls = {"first": 0, "second": 0}

    async def first(payload, manifest):
        calls["first"] += 1
        return {"ok": True, "cost_usd": 0.25}

    async def failing(payload, manifest):
        calls["second"] += 1
        return {"ok": False, "error": "temporary"}

    failed = asyncio.run(run_job(tmp_path, job["id"], {"first": first, "second": failing}))
    assert failed["status"] == "failed"
    assert failed["steps"][0]["status"] == "completed"
    assert failed["cost"] == 0.25

    async def recovered(payload, manifest):
        calls["second"] += 1
        return {"ok": True, "cost": 0.1}

    completed = asyncio.run(run_job(tmp_path, job["id"], {"first": first, "second": recovered}))
    assert completed["status"] == "completed"
    assert calls == {"first": 1, "second": 2}
    assert completed["steps"][1]["attempts"] == 2
    assert completed["cost"] == pytest.approx(0.35)


def test_job_without_registered_resume_handler_pauses(tmp_path: Path) -> None:
    job = create_job(tmp_path, "unknown-operation", ["render"])
    paused = asyncio.run(run_job(tmp_path, job["id"]))
    assert paused["status"] == "paused"
    assert "no resume handler" in paused["error"]


def test_lipsync_job_is_idempotent_and_marks_credentials(tmp_path: Path) -> None:
    output = tmp_path / "marketing" / "montage" / "out.mp4"
    first = create_lipsync_job(
        tmp_path,
        video=str(tmp_path / "in.mp4"),
        audio=str(tmp_path / "voice.wav"),
        output=str(output),
    )
    second = create_lipsync_job(
        tmp_path,
        video=str(tmp_path / "in.mp4"),
        audio=str(tmp_path / "voice.wav"),
        output=str(output),
    )
    assert first["id"] == second["id"]
    assert first["steps"][0]["requires_credentials"] is True
