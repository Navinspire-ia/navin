# Copyright (c) 2026-present Navinspire IA
# SPDX-License-Identifier: AGPL-3.0-only

"""Versioned Montage timeline storage, conversion, preview, and render jobs."""

from __future__ import annotations

import asyncio
import base64
import json
from dataclasses import asdict
from types import MethodType

import pytest
from websockets.datastructures import Headers
from websockets.http11 import Request

from navin.montage.assemble import AssembleSpec, VisualClip
from navin.montage.timeline import (
    TIMELINE_JSON_SCHEMA,
    TIMELINE_SCHEMA_VERSION,
    TimelineError,
    delete_timeline,
    get_timeline,
    list_timelines,
    preview_timeline,
    put_timeline,
    render_timeline,
    timeline_from_spec,
    timeline_to_spec,
    validate_timeline,
)


def _spec(root) -> AssembleSpec:
    return AssembleSpec(
        visuals=(
            VisualClip.from_path(root / "media" / "intro.mp4", start=1.25, end=4.75),
            VisualClip.from_path(root / "media" / "card.png", duration=2.5),
        ),
        output=str(root / "marketing" / "montage" / "exports" / "launch.mp4"),
        music=str(root / "media" / "bed.wav"),
        voice=str(root / "media" / "voice.wav"),
        subtitles=str(root / "media" / "captions.srt"),
        width=1920,
        height=1080,
        fps=24,
        music_gain_db=-18.5,
        transition="fade",
        transition_duration=0.4,
        extra_metadata={"comment": "golden", "title": "Launch"},
    )


def _prepare(root) -> None:
    (root / "media").mkdir()
    for name in ("intro.mp4", "card.png", "bed.wav", "voice.wav", "captions.srt"):
        (root / "media" / name).write_bytes(b"fixture")


def test_round_trip_golden_preserves_every_assemble_field(tmp_path) -> None:
    _prepare(tmp_path)
    spec = _spec(tmp_path)
    timeline = timeline_from_spec(spec, tmp_path, name="launch-v1")
    assert timeline == {
        "schema_version": 1,
        "name": "launch-v1",
        "visuals": [
            {
                "path": "media/intro.mp4",
                "kind": "video",
                "duration": None,
                "start": 1.25,
                "end": 4.75,
            },
            {
                "path": "media/card.png",
                "kind": "image",
                "duration": 2.5,
                "start": None,
                "end": None,
            },
        ],
        "output": "marketing/montage/exports/launch.mp4",
        "music": "media/bed.wav",
        "voice": "media/voice.wav",
        "subtitles": "media/captions.srt",
        "width": 1920,
        "height": 1080,
        "fps": 24,
        "music_gain_db": -18.5,
        "transition": "fade",
        "transition_duration": 0.4,
        "extra_metadata": {"comment": "golden", "title": "Launch"},
    }
    restored = timeline_to_spec(
        json.loads(json.dumps(timeline, sort_keys=True)), tmp_path
    )
    assert asdict(restored) == asdict(spec)


def test_schema_is_strict_and_versioned(tmp_path) -> None:
    _prepare(tmp_path)
    timeline = timeline_from_spec(_spec(tmp_path), tmp_path, name="strict")
    assert timeline["schema_version"] == TIMELINE_SCHEMA_VERSION
    assert TIMELINE_JSON_SCHEMA["properties"]["schema_version"] == {"const": 1}
    with pytest.raises(TimelineError, match="unknown fields"):
        validate_timeline({**timeline, "surprise": True}, tmp_path)
    with pytest.raises(TimelineError, match="schema_version"):
        validate_timeline({**timeline, "schema_version": 2}, tmp_path)
    bad_visual = dict(timeline["visuals"][0])
    bad_visual["unknown"] = 1
    with pytest.raises(TimelineError, match="unknown fields"):
        validate_timeline(
            {**timeline, "visuals": [bad_visual, timeline["visuals"][1]]},
            tmp_path,
        )


@pytest.mark.parametrize("name", ["../escape", "UPPER", "space name", "", "a" * 65])
def test_names_are_safe(tmp_path, name) -> None:
    _prepare(tmp_path)
    timeline = timeline_from_spec(_spec(tmp_path), tmp_path, name="safe")
    with pytest.raises(TimelineError, match="invalid timeline name"):
        put_timeline(tmp_path, name, {**timeline, "name": name})


def test_all_paths_are_sandboxed(tmp_path) -> None:
    _prepare(tmp_path)
    timeline = timeline_from_spec(_spec(tmp_path), tmp_path, name="sandbox")
    with pytest.raises(TimelineError, match="inside the workspace"):
        validate_timeline({**timeline, "output": "../escape.mp4"}, tmp_path)
    with pytest.raises(TimelineError, match="inside the workspace"):
        validate_timeline(
            {
                **timeline,
                "visuals": [{**timeline["visuals"][0], "path": "/etc/passwd"}],
            },
            tmp_path,
        )


def test_atomic_crud_uses_timelines_directory(tmp_path) -> None:
    _prepare(tmp_path)
    timeline = timeline_from_spec(_spec(tmp_path), tmp_path, name="launch")
    assert put_timeline(tmp_path, "launch", timeline) == timeline
    stored = tmp_path / "marketing" / "montage" / "timelines" / "launch.json"
    assert stored.is_file()
    assert get_timeline(tmp_path, "launch") == timeline
    assert [row["name"] for row in list_timelines(tmp_path)] == ["launch"]
    assert delete_timeline(tmp_path, "launch") == {"ok": True, "name": "launch"}
    with pytest.raises(TimelineError, match="not found"):
        get_timeline(tmp_path, "launch")


def test_preview_runs_ffmpeg_runner_and_publishes_thumbnail(
    tmp_path, monkeypatch
) -> None:
    _prepare(tmp_path)
    timeline = timeline_from_spec(_spec(tmp_path), tmp_path, name="preview")
    put_timeline(tmp_path, "preview", timeline)
    monkeypatch.setattr("navin.montage.detect.find_ffmpeg", lambda: "/fake/ffmpeg")
    captured = {}

    async def fake_run(argv, **kwargs):
        from navin.montage.ffmpeg_runner import ProcessResult

        captured["argv"] = list(argv)
        output = tmp_path / argv[-1]
        output.parent.mkdir(parents=True, exist_ok=True)
        output.write_bytes(b"jpeg")
        return ProcessResult(True, tuple(argv), "ffmpeg", 0, "", "", "", 0.1)

    monkeypatch.setattr("navin.montage.timeline.run_process", fake_run)
    result = asyncio.run(preview_timeline(tmp_path, "preview"))
    assert result["mime"] == "image/jpeg"
    assert (tmp_path / result["path"]).read_bytes() == b"jpeg"
    assert "-frames:v" in captured["argv"]


def test_render_is_a_durable_timeline_job(tmp_path, monkeypatch) -> None:
    _prepare(tmp_path)
    timeline = timeline_from_spec(_spec(tmp_path), tmp_path, name="render")
    put_timeline(tmp_path, "render", timeline)

    captured: dict[str, object] = {}

    async def fake_assemble(spec, **hooks):
        captured.update(hooks)
        return {"ok": True, "output": spec.output}

    monkeypatch.setattr("navin.montage.timeline.run_assemble", fake_assemble)
    job = asyncio.run(render_timeline(tmp_path, "render"))
    assert job["operation"] == "timeline-render"
    assert job["status"] == "completed"
    assert job["steps"][0]["status"] == "completed"
    manifest = tmp_path / "marketing" / "montage" / "jobs" / f"{job['id']}.json"
    assert manifest.is_file()
    # The render step is wired to the job's live context (progress + cancel).
    assert callable(captured.get("on_progress"))
    assert isinstance(captured.get("cancel"), asyncio.Event)


def _route_handler(root):
    from navin.webui.ws_http import GatewayHTTPHandler

    handler = object.__new__(GatewayHTTPHandler)
    handler.check_api_token = MethodType(lambda self, request: True, handler)
    handler._montage_workspace_root = MethodType(
        lambda self, request: str(root), handler
    )
    return handler


def _request(path: str, body: dict | None = None) -> Request:
    headers = Headers()
    if body is not None:
        encoded = base64.b64encode(json.dumps(body).encode()).decode()
        headers["x-navin-file-body-0"] = encoded
    return Request(path, headers)


def test_ws_http_routes_list_get_put_and_delete(tmp_path) -> None:
    _prepare(tmp_path)
    document = timeline_from_spec(_spec(tmp_path), tmp_path, name="routes")
    handler = _route_handler(tmp_path)

    async def dispatch(path, body=None):
        return await handler._dispatch_misc_routes(None, _request(path, body), path)

    put = asyncio.run(
        dispatch("/api/webui/montage/timelines/routes/put", document)
    )
    assert put.status_code == 200
    listed = asyncio.run(dispatch("/api/webui/montage/timelines"))
    assert json.loads(listed.body)["count"] == 1
    fetched = asyncio.run(dispatch("/api/webui/montage/timelines/routes"))
    assert json.loads(fetched.body)["name"] == "routes"
    deleted = asyncio.run(
        dispatch("/api/webui/montage/timelines/routes/delete")
    )
    assert json.loads(deleted.body)["ok"] is True


def test_ws_http_routes_preview_and_render(tmp_path, monkeypatch) -> None:
    handler = _route_handler(tmp_path)

    async def fake_preview(root, name):
        return {"ok": True, "name": name, "path": "preview.jpg"}

    async def fake_render(root, name, *, output=None, notify=None):
        return {"status": "completed", "name": name, "output": output}

    def fake_start(root, name, *, output=None, notify=None):
        return {"status": "pending", "id": "job-1", "name": name, "output": output}

    monkeypatch.setattr(
        "navin.webui.montage_api.preview_montage_timeline", fake_preview
    )
    monkeypatch.setattr(
        "navin.webui.montage_api.render_montage_timeline", fake_render
    )
    monkeypatch.setattr(
        "navin.webui.montage_api.start_montage_timeline_render", fake_start
    )
    preview_path = "/api/webui/montage/timelines/demo/preview"
    preview = asyncio.run(
        handler._dispatch_misc_routes(None, _request(preview_path), preview_path)
    )
    assert json.loads(preview.body)["path"] == "preview.jpg"
    # Default: the render is a background job the UI follows through /jobs.
    render_path = (
        "/api/webui/montage/timelines/demo/render"
        "?output=marketing%2Fmontage%2Fexports%2Fother.mp4"
    )
    render = asyncio.run(
        handler._dispatch_misc_routes(
            None,
            _request(render_path),
            "/api/webui/montage/timelines/demo/render",
        )
    )
    started = json.loads(render.body)
    assert started["status"] == "pending"
    assert started["output"] == "marketing/montage/exports/other.mp4"
    # wait=1 keeps the request open until ffmpeg is done (scripts, tests).
    waited = asyncio.run(
        handler._dispatch_misc_routes(
            None,
            _request(render_path + "&wait=1"),
            "/api/webui/montage/timelines/demo/render",
        )
    )
    assert json.loads(waited.body)["status"] == "completed"


def test_ws_http_routes_cancel_and_probe(tmp_path, monkeypatch) -> None:
    from navin.montage.jobs import create_job

    handler = _route_handler(tmp_path)
    job = create_job(tmp_path, "timeline-render", ["render"], payload={"timeline": "demo"})
    cancel_path = f"/api/webui/montage/jobs/{job['id']}/cancel"
    cancelled = asyncio.run(
        handler._dispatch_misc_routes(None, _request(cancel_path), cancel_path)
    )
    body = json.loads(cancelled.body)
    assert body["status"] == "cancelled"
    assert body["steps"][0]["status"] == "cancelled"

    async def fake_probe(root, path):
        return {"path": path, "duration": 4.5, "kind": "video"}

    monkeypatch.setattr("navin.webui.montage_api.probe_montage_media", fake_probe)
    probe_path = "/api/webui/montage/probe?file=marketing%2Fmontage%2Fdemos%2Fa.mp4"
    probed = asyncio.run(
        handler._dispatch_misc_routes(None, _request(probe_path), "/api/webui/montage/probe")
    )
    assert json.loads(probed.body)["duration"] == 4.5
    missing = asyncio.run(
        handler._dispatch_misc_routes(
            None,
            _request("/api/webui/montage/jobs/nope/cancel"),
            "/api/webui/montage/jobs/nope/cancel",
        )
    )
    assert missing.status_code == 404
