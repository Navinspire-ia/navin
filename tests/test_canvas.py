# Copyright (c) 2026-present Navinspire IA
# SPDX-License-Identifier: AGPL-3.0-only

"""Navin canvas: kit, block validation, templates, present_artifact canvas."""

from __future__ import annotations

import asyncio
import json
import re
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

import pytest

from navin.agent.tools.context import RequestContext, request_context
from navin.agent.tools.present_artifact import PresentArtifactTool
from navin.artifacts.canvas import (
    CANVAS_MARKER,
    CanvasError,
    build_canvas,
    is_canvas_html,
    kit_css,
    kit_js,
    validate_blocks,
)
from navin.artifacts.canvas.templates import TEMPLATES, build_template
from navin.artifacts.store import ArtifactStore

# --------------------------------------------------------------------------
# Real-shaped sample data (keys mirror the producing Navin modules)
# --------------------------------------------------------------------------


def _bench(scale: float) -> dict:
    def m(p50, p95, mx, n=120):
        return {"samples": n, "p50_ms": round(p50 * scale, 2), "p95_ms": round(p95 * scale, 2), "max_ms": round(mx * scale, 2)}

    return {
        "passed": scale < 1, "scope": "tui", "platform": "linux", "python": "3.12.3", "textual": "3.2.0",
        "load": {"tools": 40, "runs": 3},
        "metrics": {
            "input": m(11.2, 38.0, 61.0), "scheduled_input": m(14.0, 44.5, 120.0),
            "open": m(42.0, 88.0, 140.0, 30), "close": m(18.0, 61.0, 95.0, 30), "loop": m(3.1, 9.4, 22.0, 900),
        },
        "stream_characters_per_run": [182000, 181500, 182200],
    }


BENCH = {"before": _bench(1.6), "after": _bench(1.0), "label_before": "main", "label_after": "prod-v2"}

FINDINGS = {
    "kind": "full",
    "counts": {"critical": 1, "high": 1, "medium": 1, "low": 0, "info": 1},
    "tools": [{"tool": "gitleaks", "available": True, "count": 1}, {"tool": "semgrep", "available": False}],
    "findings": [
        {"id": "f1", "severity": "critical", "category": "Secrets", "summary": "AWS key committed",
         "explanation": "A live access key is in the repo.", "recommendation": "Rotate and move to env.",
         "file_path": "deploy/env.sh", "line": 12, "source": "gitleaks",
         "malicious_input_example": "AKIA****************", "confidence": 0.98},
        {"id": "f2", "severity": "high", "category": "bug", "summary": "Unawaited coroutine drops errors",
         "explanation": "send() is never awaited.", "recommendation": "await it.", "file_path": "navin/bus/queue.py",
         "start_line": 88, "end_line": 90, "existing_code": "self.send(msg)", "confidence": 0.9, "status": "fixed"},
        {"id": "f3", "severity": "nit", "category": "style", "summary": "Unused import", "file_path": "navin/x.py",
         "start_line": 3, "existing_code": "import os"},
        {"id": "f4", "severity": "medium", "summary": "Missing timeout", "explanation": "requests.get without timeout"},
    ],
}

PR = {
    "pr": {"forge": "github", "repo": "navinspire/navin", "request": {
        "url": "https://github.com/navinspire/navin/pull/812", "number": 812, "title": "Canvas kit",
        "state": "open", "is_draft": False, "head": "canvas", "base": "main", "merged": False, "merged_at": None}},
    "ci": {"forge": "github", "branch": "canvas", "checks": {
        "state": "failure", "total": 4, "passing": 2, "failing": 1, "pending": 1, "items": [
            {"name": "pytest", "state": "success", "url": "u"}, {"name": "vitest", "state": "success", "url": "u"},
            {"name": "build-desktop", "state": "failure", "url": "u"}, {"name": "e2e", "state": "in_progress", "url": "u"}]}},
    "threads": [
        {"path": "navin/artifacts/canvas/kit.js", "line": 40, "resolved": False, "author": "rev", "body": "Escape labels"},
        {"path": "README.md", "line": 2, "resolved": True},
    ],
}

BOARD = {
    "schema_version": 1, "project_path": "/home/u/projects/shop",
    "statuses": ["backlog", "planned", "in_progress", "review", "audit", "fix", "blocked", "cancelled", "done"],
    "priorities": ["low", "medium", "high", "critical"],
    "tasks": [
        {"id": "T-1", "title": "Schema", "status": "done", "priority": "high", "depends_on": [], "milestone_id": "M-1",
         "updated_at": "2026-10-10T10:00:00Z"},
        {"id": "T-2", "title": "API", "status": "in_progress", "priority": "high", "depends_on": ["T-1"],
         "milestone_id": "M-1", "updated_at": "2026-10-15T10:00:00Z"},
        {"id": "T-3", "title": "UI", "status": "planned", "priority": "medium", "depends_on": ["T-2"],
         "milestone_id": "M-2", "updated_at": "2026-10-16T10:00:00Z"},
        {"id": "T-4", "title": "Docs", "status": "blocked", "priority": "low", "depends_on": [], "updated_at": "2026-10-17T10:00:00Z"},
    ],
    "milestones": [
        {"id": "M-1", "title": "Beta", "target_date": "2026-11-01", "status": "active"},
        {"id": "M-2", "title": "Launch", "target_date": None, "status": "planned"},
    ],
    "activity": [],
    "plan": {"tasks": {}, "ready_queue": [], "blocked": [{"id": "T-4", "blocked_by": [], "status": "blocked"}],
             "critical_path": ["T-2", "T-3"], "in_progress": ["T-2"], "open_count": 3, "done_count": 1,
             "cycles": [], "dangling": []},
    "milestone_progress": {"M-1": {"total": 2, "done": 1, "open": 1, "blocked": 0}, "M-2": {"total": 1, "done": 0, "open": 1, "blocked": 0}},
}


def _day(date: str, p: int, c: int, k: int) -> dict:
    return {"date": date, "prompt_tokens": p, "completion_tokens": c, "cached_tokens": k, "total_tokens": p + c,
            "provider_tokens": p + c, "estimated_tokens": 0, "requests": 12, "provider_requests": 12,
            "estimated_requests": 0, "sources": {}, "models": {}}


USAGE = {
    "days": [_day(f"2026-10-{d:02d}", 40000 + d * 900, 8000 + d * 100, 20000) for d in range(1, 20)],
    "total_tokens": 9_000_000, "total_tokens_30d": 1_100_000, "total_tokens_365d": 9_000_000,
    "peak_day_tokens": 65000, "current_streak_days": 19, "longest_streak_days": 40, "active_days_30d": 19,
    "requests_30d": 228, "month": "2026-10",
    "models_month": [
        {"model": "claude-opus-5-5", "requests": 150, "prompt_tokens": 700000, "completion_tokens": 120000, "cached_tokens": 300000, "total_tokens": 820000},
        {"model": "gpt-5.1-mini", "requests": 78, "prompt_tokens": 200000, "completion_tokens": 40000, "cached_tokens": 0, "total_tokens": 240000},
    ],
    "month_total": {"requests": 228, "prompt_tokens": 900000, "completion_tokens": 160000, "cached_tokens": 300000, "total_tokens": 1060000},
    "prices": {"claude-opus-5-5": {"prompt": 5, "completion": 25, "cached": 0.5}},
}

VIDEO_QA = {
    "schema_version": 1, "created_at": "2026-10-19T09:00:00Z",
    "video": {"width": 1920, "height": 1080, "fps": 30.0, "duration": 42.5, "has_audio": True,
              "video_codec": "h264", "audio_codec": "aac", "path": "/w/out/promo.mp4"},
    "requirements": {},
    "policy": {"target_lufs": -14.0, "lufs_tolerance": 2.0, "max_true_peak_db": -1.0, "pass_score": 85, "warn_score": 65},
    "provider": {"provider": "gateway", "model": "vision-1", "usage": {}, "cost_usd": 0.02},
    "events": {"black": [{"start": 0.0, "end": 0.4, "duration": 0.4}], "freezes": [{"start": 18.2, "end": 19.9, "duration": 1.7}],
               "silences": [], "crop": None, "loudness": {"integrated": -17.3, "lra": 6.1, "true_peak": -0.4}},
    "frames": [{"path": "/w/f1.jpg", "timestamp": 3.0, "scene_index": 0}, {"path": "/w/f2.jpg", "timestamp": 19.0, "scene_index": 2}],
    "contact_sheet": "/w/sheet.jpg",
    "scores": {"objective": 72, "vision": 81, "overall": 76},
    "verdict": "WARN",
    "findings": [
        {"check": "resolution", "source": "objective", "status": "PASS", "score": 100, "evidence": ["1920x1080"], "recommendation": "", "scene_index": None, "timestamp": None, "end": None},
        {"check": "frozen_frames", "source": "objective", "status": "WARN", "score": 60, "evidence": ["1.7s freeze"],
         "recommendation": "Re-render scene 2", "scene_index": 2, "timestamp": 18.2, "end": 19.9},
        {"check": "loudness", "source": "objective", "status": "BLOCK", "score": 40, "evidence": ["-17.3 LUFS"],
         "recommendation": "Normalize to -14 LUFS", "scene_index": None, "timestamp": None, "end": None},
    ],
    "pass": False, "scenes_to_fix": {"2": 1},
}

SAMPLES = {"benchmark": BENCH, "findings": FINDINGS, "pr_status": PR, "board": BOARD, "token_usage": USAGE, "video_qa": VIDEO_QA}

_EMOJI = re.compile("[\U0001F000-\U0001FAFF☀-➿️]")


def _charts(blocks: list[dict]) -> list[dict]:
    out = []
    for b in blocks:
        if b["type"] in {"bar_chart", "line_chart"}:
            out.append(b)
        if b["type"] == "section":
            out.extend(_charts(b["blocks"]))
    return out


def _island(html: str) -> dict:
    raw = html.split('id="navin-canvas-data">', 1)[1].split("</script>", 1)[0]
    return json.loads(raw)


# --------------------------------------------------------------------------
# Validation
# --------------------------------------------------------------------------

CHART = {"type": "bar_chart", "title": "T", "x_label": "X", "y_label": "Y", "unit": "ms", "caption": "Source: test",
         "categories": ["a", "b"], "series": [{"name": "s", "values": [1, 2]}]}


def test_valid_chart_passes():
    validate_blocks([CHART])


@pytest.mark.parametrize("missing", ["title", "x_label", "y_label", "unit", "caption"])
def test_unlabeled_chart_rejected(missing):
    block = {k: v for k, v in CHART.items() if k != missing}
    with pytest.raises(CanvasError, match=missing):
        validate_blocks([block])


def test_multi_series_needs_unique_names_for_legend():
    block = {**CHART, "series": [{"name": "s", "values": [1, 2]}, {"values": [3, 4]}]}
    with pytest.raises(CanvasError, match="legend"):
        validate_blocks([block])
    block = {**CHART, "series": [{"name": "s", "values": [1, 2]}, {"name": "s", "values": [3, 4]}]}
    with pytest.raises(CanvasError, match="unique"):
        validate_blocks([block])


@pytest.mark.parametrize("blocks", [
    [],
    [{"type": "stats", "items": []}],
    [{"type": "table", "columns": [{"key": "a", "label": "A"}], "rows": []}],
    [{"type": "findings", "items": []}],
    [{"type": "section", "title": "S", "blocks": []}],
    [{**CHART, "series": []}],
    [{"type": "text", "text": "  "}],
    [{"type": "diff", "before": {"label": "a", "text": ""}, "after": {"label": "b", "text": ""}}],
])
def test_empty_blocks_rejected(blocks):
    with pytest.raises(CanvasError):
        validate_blocks(blocks)


def test_unknown_type_and_bad_values_rejected():
    with pytest.raises(CanvasError, match="unknown block type 'pie'"):
        validate_blocks([{"type": "pie"}])
    with pytest.raises(CanvasError, match="finite"):
        validate_blocks([{**CHART, "series": [{"name": "s", "values": [1, float("nan")]}]}])
    with pytest.raises(CanvasError, match="entries"):
        validate_blocks([{**CHART, "series": [{"name": "s", "values": [1]}]}])
    with pytest.raises(CanvasError, match="severity"):
        validate_blocks([{"type": "findings", "items": [{"severity": "urgent", "title": "x"}]}])


def test_errors_are_collected_and_title_required():
    with pytest.raises(CanvasError) as exc:
        build_canvas("", [{"type": "pie"}, {"type": "stats", "items": []}])
    assert len(exc.value.problems) == 3


# --------------------------------------------------------------------------
# HTML output, style lint
# --------------------------------------------------------------------------


def test_build_canvas_is_self_contained_and_escaped():
    html = build_canvas("Report </script><b>", [{"type": "text", "text": "</script><img src=x onerror=alert(1)>"}])
    assert is_canvas_html(html) and CANVAS_MARKER in html
    assert "default-src 'none'" in html
    assert "<title>Report &lt;/script&gt;&lt;b&gt;</title>" in html
    body = html.split("<body>", 1)[1]
    assert "<img" not in body and body.count("</script>") == 2
    assert _island(html)["blocks"][0]["text"].startswith("</script>")
    assert not re.search(r"""(src|href)\s*=\s*["']?(https?:)?//""", html)
    assert "@import" not in html


def test_style_lint_kit_and_outputs():
    sources = {"kit.css": kit_css(), "kit.js": kit_js()}
    for name, data in SAMPLES.items():
        title, blocks = build_template(name, data)
        sources[f"template:{name}"] = build_canvas(title, blocks, generated_at="2026-10-19 10:00 UTC")
    for label, text in sources.items():
        lowered = text.lower()
        for banned in ("gradient", "box-shadow", "text-shadow", "drop-shadow"):
            assert banned not in lowered, f"{banned} in {label}"
        assert not _EMOJI.search(text), f"emoji in {label}"
        assert "\u2014" not in text and "\u2013" not in text, f"dash in {label}"


# --------------------------------------------------------------------------
# Templates
# --------------------------------------------------------------------------


@pytest.mark.parametrize("name", sorted(TEMPLATES))
def test_template_builds_labelled_canvas(name):
    title, blocks = build_template(name, SAMPLES[name])
    assert title
    html = build_canvas(title, blocks, generated_at="2026-10-19 10:00 UTC")
    doc = _island(html)
    charts = _charts(doc["blocks"])
    assert charts, f"{name} has no chart"
    for chart in charts:
        for key in ("title", "x_label", "y_label", "unit", "caption"):
            assert str(chart[key]).strip(), (name, key)
        assert all(s["name"] for s in chart["series"])


def test_template_contents():
    _, bench = build_template("benchmark", BENCH)
    table = next(b for b in bench if b["type"] == "table")
    row = next(r for r in table["rows"] if r["metric"] == "input")
    assert row["p95_change"] == pytest.approx(-37.5, abs=0.1)
    assert row["budget"]["tone"] == "good"
    chart = next(b for b in bench if b["type"] == "bar_chart")
    assert chart["series"][0]["role"] == "baseline"

    _, findings = build_template("findings", FINDINGS)
    items = next(b for b in findings if b["type"] == "findings")["items"]
    assert {"severity": "critical", "file": "deploy/env.sh", "line": 12} .items() <= items[0].items()
    assert items[1]["status"] == "fixed" and items[2]["severity"] == "info"

    title, pr = build_template("pr_status", PR)
    assert title == "PR #812: Canvas kit"
    stats = pr[0]["items"]
    assert stats[0]["value"] == "blocked" and "1 failing" in stats[0]["hint"]
    assert next(b for b in pr if b["type"] == "findings")["items"][0]["line"] == 40

    title, board = build_template("board", BOARD)
    assert title == "Project board: shop"
    crit = next(b for b in board if b.get("title") == "Critical path")
    assert [i["label"] for i in crit["items"]] == ["API", "UI"]
    ms = next(b for b in board if b.get("title") == "Milestones")["items"]
    assert ms[1]["date"] == "unscheduled"

    _, usage = build_template("token_usage", USAGE)
    cost = next(s for s in usage[0]["items"] if s["label"].startswith("Estimated cost"))
    # (700k - 300k cached) * 5 + 120k * 25 + 300k * 0.5, per million
    assert cost["value"] == pytest.approx(5.15)

    title, qa = build_template("video_qa", VIDEO_QA)
    assert title == "Video QA: promo.mp4"
    issues = next(b for b in qa if b["type"] == "findings")["items"]
    assert issues[0]["location"] == "0:18.20 - 0:19.90, scene 2"
    assert {i["severity"] for i in issues} == {"medium", "high"}


def test_templates_reject_empty_data():
    with pytest.raises(CanvasError):
        build_template("findings", {"findings": []})
    with pytest.raises(CanvasError):
        build_template("video_qa", {"verdict": "SKIPPED", "reason": "ffmpeg missing"})
    with pytest.raises(CanvasError, match="unknown template"):
        build_template("nope", {})
    with pytest.raises(CanvasError, match="needs data"):
        build_template("pr_status", None)


def test_local_loaders(tmp_path):
    with patch("navin.webui.token_usage.token_usage_payload", return_value=USAGE):
        title, _ = build_template("token_usage", None)
    assert title == "Token usage"
    from navin.board.store import ProjectBoardStore

    with pytest.raises(CanvasError, match="no tasks"):
        build_template("board", None, workspace=str(tmp_path))
    store = ProjectBoardStore(tmp_path)
    first = store.create_task(title="First", actor="navin", actor_type="agent", status="done")
    store.create_task(title="Second", actor="navin", actor_type="agent", depends_on=[first["id"]])
    title, blocks = build_template("board", None, workspace=str(tmp_path))
    assert title == f"Project board: {tmp_path.name}"
    build_canvas(title, blocks)


# --------------------------------------------------------------------------
# present_artifact type=canvas
# --------------------------------------------------------------------------


class _Bus:
    def __init__(self) -> None:
        self.sent: list = []
        self.outbound = SimpleNamespace(put_nowait=self.sent.append)


def _run(tool: PresentArtifactTool, **kwargs):
    ctx = RequestContext(channel="websocket", chat_id="chat-canvas")
    async def go():
        with request_context(ctx):
            return await tool.execute(**kwargs)
    return asyncio.run(go())


@pytest.fixture
def store_root(tmp_path, monkeypatch):
    monkeypatch.setattr(Path, "home", lambda: tmp_path)
    return tmp_path


def test_present_artifact_canvas_blocks(store_root):
    tool = PresentArtifactTool(bus=_Bus(), working_dir=str(store_root))
    out = _run(tool, type="canvas", title="Latency", blocks=[CHART])
    assert "Presented 1 artifact(s)" in str(out) and "(html)" in str(out)
    art = ArtifactStore("chat-canvas").list()[0]
    content = ArtifactStore("chat-canvas").get(art["id"])["content"]
    assert is_canvas_html(content) and art["title"] == "Latency"


def test_present_artifact_canvas_template_and_errors(store_root):
    (store_root / "qa.json").write_text(json.dumps(VIDEO_QA), encoding="utf-8")
    tool = PresentArtifactTool(bus=_Bus(), working_dir=str(store_root))
    out = _run(tool, type="canvas", template="video_qa", data_path="qa.json")
    assert "Video QA: promo.mp4" in str(out)
    bad = _run(tool, type="canvas", title="x", blocks=[{**CHART, "caption": ""}])
    assert "caption" in str(bad) and "Error" in str(bad)
    missing = _run(tool, type="canvas", title="x")
    assert "needs blocks" in str(missing)


def test_tool_schema_lists_canvas():
    params = PresentArtifactTool().parameters
    assert "canvas" in params["properties"]["type"]["enum"]
    assert set(params["properties"]["template"]["enum"]) == set(TEMPLATES)
