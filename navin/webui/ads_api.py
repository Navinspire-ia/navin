# Copyright (c) 2026-present Navinspire IA
# SPDX-License-Identifier: AGPL-3.0-only

"""Ads desk API: import platform exports, analyze, score, approve changes, export.

Same engine as the ``ads`` agent tool (``navin.ads``). The desk keeps its book
under the runtime data dir: uploaded exports, the latest analysis, and the
approval-gated change store.
"""

from __future__ import annotations

import base64
import binascii
import json
import os
import re
import threading
import time
import uuid
from pathlib import Path
from typing import Any

from navin.ads.changes import ChangeStore, mcp_execution_plan
from navin.ads.ingest import ingest_file
from navin.ads.models import AdsAnalysis
from navin.ads.pipeline import analyze_ingested, compact_payload
from navin.ads.report import render_html, render_markdown, report_payload
from navin.ads.scoring import health_score

_MAX_UPLOAD = 25 * 1024 * 1024
_ALLOWED = {".csv", ".tsv", ".txt", ".xlsx"}
_PLATFORMS = {"google", "microsoft", "meta", "linkedin", "tiktok", "reddit"}
_lock = threading.Lock()


class AdsDeskError(Exception):
    def __init__(self, message: str, *, status: int = 400) -> None:
        super().__init__(message)
        self.message = message
        self.status = status


def _root() -> Path:
    from navin.config.paths import get_runtime_subdir

    return get_runtime_subdir("ads-desk")


def _config():
    from navin.config.loader import load_config

    return load_config().tools.ads


def _read_json(path: Path, default: Any) -> Any:
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return default


def _write_json(path: Path, payload: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temp = path.with_suffix(path.suffix + ".tmp")
    temp.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    os.replace(temp, path)


def _exports() -> list[dict[str, Any]]:
    return _read_json(_root() / "exports.json", [])


def _store() -> ChangeStore:
    return ChangeStore(_root() / "changes.jsonl")


def _settings() -> dict[str, Any]:
    return _read_json(_root() / "settings.json", {"currency": "", "monthly_budget": None})


def _changes_summary() -> dict[str, Any]:
    rows = _store().list()
    by_status: dict[str, int] = {}
    for row in rows:
        by_status[str(row.get("status"))] = by_status.get(str(row.get("status")), 0) + 1
    return {"count": len(rows), "by_status": by_status}


def snapshot() -> dict[str, Any]:
    analysis = _read_json(_root() / "analysis.json", None)
    return {
        "exports": _exports(),
        "analysis": analysis,
        "changes": _changes_summary(),
        "settings": _settings(),
    }


def ingest(body: dict[str, Any]) -> dict[str, Any]:
    filename = os.path.basename(str(body.get("filename") or "")).strip()
    suffix = Path(filename).suffix.lower()
    if not filename or suffix not in _ALLOWED:
        raise AdsDeskError("Import a CSV, TSV or XLSX export from the ads platform.")
    try:
        data = base64.b64decode(str(body.get("data_b64") or ""), validate=True)
    except (binascii.Error, ValueError) as exc:
        raise AdsDeskError("The file could not be read.") from exc
    if not data:
        raise AdsDeskError("The file is empty.")
    if len(data) > _MAX_UPLOAD:
        raise AdsDeskError("The file is larger than 25 MB; export a shorter period or aggregate it.")
    platform = str(body.get("platform") or "auto").lower()
    export_id = uuid.uuid4().hex[:12]
    path = _root() / "exports" / f"{export_id}{suffix}"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(data)
    try:
        result = ingest_file(path, platform=platform if platform in _PLATFORMS else None)
    except Exception as exc:  # noqa: BLE001 - parser errors become a readable refusal
        path.unlink(missing_ok=True)
        raise AdsDeskError(f"This export could not be parsed: {exc}") from exc
    if not result.rows:
        path.unlink(missing_ok=True)
        raise AdsDeskError("No data rows were found in this export. Check it contains campaign rows.")
    entry = {
        "id": export_id,
        "filename": filename,
        "path": path.name,
        "platform": result.platform.value,
        "rows": len(result.rows),
        "levels": sorted({row.level().value for row in result.rows}),
        "unknown_columns": result.unknown_columns,
        "warnings": result.warnings,
        "uploaded_at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
        "active": True,
    }
    with _lock:
        _write_json(_root() / "exports.json", [entry, *_exports()])
    return {"export": entry}


def delete_export(body: dict[str, Any]) -> dict[str, Any]:
    export_id = str(body.get("id") or "")
    with _lock:
        rows = _exports()
        kept = [row for row in rows if row["id"] != export_id]
        if len(kept) == len(rows):
            raise AdsDeskError("Unknown export.", status=404)
        for row in rows:
            if row["id"] == export_id:
                (_root() / "exports" / row["path"]).unlink(missing_ok=True)
        _write_json(_root() / "exports.json", kept)
    return {"deleted": export_id}


def toggle_export(body: dict[str, Any]) -> dict[str, Any]:
    export_id = str(body.get("id") or "")
    with _lock:
        rows = _exports()
        for row in rows:
            if row["id"] == export_id:
                row["active"] = bool(body.get("active", True))
                _write_json(_root() / "exports.json", rows)
                return {"export": row}
    raise AdsDeskError("Unknown export.", status=404)


def _analysis_model() -> AdsAnalysis:
    payload = _read_json(_root() / "analysis-full.json", None)
    if not payload:
        raise AdsDeskError("Run an analysis first.", status=409)
    return AdsAnalysis.model_validate(payload)


def analyze(body: dict[str, Any]) -> dict[str, Any]:
    cfg = _config()
    wanted = {str(item) for item in body.get("export_ids") or []}
    rows = [row for row in _exports() if (row["id"] in wanted if wanted else row.get("active", True))]
    if not rows:
        raise AdsDeskError("Import at least one export (Google, Microsoft, Meta, LinkedIn, TikTok or Reddit).")
    results = [
        ingest_file(_root() / "exports" / row["path"], platform=row["platform"] if row["platform"] in _PLATFORMS else None)
        for row in rows
    ]
    total = sum(len(item.rows) for item in results)
    if total > cfg.max_rows:
        raise AdsDeskError(f"Too many rows ({total}); aggregate the export first.")
    settings = _settings()
    if "currency" in body:
        settings["currency"] = str(body.get("currency") or "").upper()[:8]
    if "monthly_budget" in body:
        budget = body.get("monthly_budget")
        settings["monthly_budget"] = float(budget) if budget not in (None, "") else None
    _write_json(_root() / "settings.json", settings)
    analysis = analyze_ingested(
        results,
        thresholds=cfg.thresholds,
        monthly_budget=settings.get("monthly_budget"),
        currency=settings.get("currency") or cfg.currency or "",
    )
    stored = _store().upsert_proposals(analysis.changes)
    payload = compact_payload(report_payload(analysis), limit=200)
    payload["analyzed_at"] = time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())
    payload["changes_store"] = stored
    payload["export_ids"] = [row["id"] for row in rows]
    _write_json(_root() / "analysis-full.json", analysis.model_dump(mode="json"))
    _write_json(_root() / "analysis.json", payload)
    return {"analysis": payload, "changes": _changes_summary()}


def score(_body: dict[str, Any]) -> dict[str, Any]:
    return {"health": health_score(_analysis_model())}


def export_report(body: dict[str, Any]) -> dict[str, Any]:
    analysis = _analysis_model()
    fmt = str(body.get("format") or "html")
    if fmt == "md":
        content, mime = render_markdown(analysis), "text/markdown"
    elif fmt == "json":
        content, mime = json.dumps(report_payload(analysis), ensure_ascii=False, indent=2), "application/json"
    else:
        fmt, content, mime = "html", render_html(analysis), "text/html"
    return {"filename": f"ads-report.{fmt}", "mime": mime, "content": content}


def _ids(body: dict[str, Any]) -> list[str]:
    raw = body.get("ids") or []
    if isinstance(raw, str):
        raw = re.split(r"[,\s]+", raw)
    ids = [str(item).strip() for item in raw if str(item).strip()]
    if not ids:
        raise AdsDeskError("Select at least one change.")
    return ids


def list_changes(body: dict[str, Any]) -> dict[str, Any]:
    status = str(body.get("status") or "") or None
    rows = _store().list(status=status)
    approved = [row for row in rows if row.get("status") == "approved"]
    return {
        "changes": rows,
        **_changes_summary(),
        "mcp_plan": mcp_execution_plan(approved) if approved else None,
    }


def set_status(body: dict[str, Any], status: str) -> dict[str, Any]:
    ids = _ids(body)
    note = str(body.get("note") or "").strip()[:2000]
    proof = str(body.get("proof") or "").strip()[:4000]
    if status == "applied" and not proof:
        raise AdsDeskError(
            "Paste what the platform shows after the change (read-back) before marking it applied."
        )
    try:
        result = _store().set_status(ids, status, note=note, proof=proof)  # type: ignore[arg-type]
    except ValueError as exc:
        raise AdsDeskError(str(exc), status=409) from exc
    return {**result, **list_changes({})}


def export_changes(body: dict[str, Any]) -> dict[str, Any]:
    fmt = str(body.get("format") or "csv")
    if fmt not in {"csv", "google_editor", "microsoft_bulk"}:
        raise AdsDeskError("Format must be csv, google_editor or microsoft_bulk.")
    platform = {"google_editor": "google", "microsoft_bulk": "microsoft"}.get(fmt)
    raw_ids = body.get("ids")
    ids = _ids(body) if raw_ids else None
    filename, text = _store().export(platform=platform, format=fmt, ids=ids)
    rows = max(0, text.count("\n") - 1)
    if rows == 0:
        raise AdsDeskError("No approved change to export for this platform yet.", status=409)
    return {"filename": filename, "mime": "text/csv", "content": text, "rows": rows}


def handle_ads_action(action: str, body: dict[str, Any]) -> dict[str, Any]:
    handlers = {
        "snapshot": lambda _b: snapshot(),
        "ingest": ingest,
        "exports.delete": delete_export,
        "exports.toggle": toggle_export,
        "analyze": analyze,
        "score": score,
        "report": export_report,
        "changes.list": list_changes,
        "changes.approve": lambda b: set_status(b, "approved"),
        "changes.reject": lambda b: set_status(b, "rejected"),
        "changes.mark_applied": lambda b: set_status(b, "applied"),
        "changes.reset": lambda b: set_status(b, "proposed"),
        "export_changes": export_changes,
    }
    handler = handlers.get(action)
    if handler is None:
        raise AdsDeskError(f"Unknown Ads action {action!r}.", status=404)
    return handler(body or {})
