"""Workspace-scoped WebUI API for Marketing visual QA."""

from __future__ import annotations

import asyncio
import json
import re
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from navin.agent.tools.visual_qa import visual_qa_readiness
from navin.marketing.visual_qa import (
    VisualQAPolicy,
    run_visual_qa,
    write_visual_qa_report,
)
from navin.providers.visual_qa import (
    get_visual_qa_provider,
    visual_qa_credentials_ready,
)
from navin.utils.atomic_io import atomic_write_text
from navin.utils.llm_runtime import runtime_from_provider_snapshot

_IMAGE_EXTENSIONS = {".png", ".jpg", ".jpeg", ".webp", ".gif", ".bmp", ".tif", ".tiff"}
_REPORT_ID = re.compile(r"^[A-Za-z0-9._-]{1,160}$")
_VERDICTS = {"PASS", "WARN", "BLOCK"}
_SKIP_PARTS = {".git", ".navin", "node_modules", "__pycache__", ".venv"}


class MarketingQAApiError(ValueError):
    def __init__(self, message: str, *, status: int = 400) -> None:
        super().__init__(message)
        self.message = message
        self.status = status


def _require_workspace(workspace_root: str | Path | None) -> Path:
    if not workspace_root:
        raise MarketingQAApiError("workspace root is required")
    root = Path(workspace_root).expanduser().resolve()
    if not root.is_dir():
        raise MarketingQAApiError("workspace root not found", status=404)
    return root


def _qa_dir(root: Path) -> Path:
    return root / "marketing" / "qa"


def _resolve_asset(root: Path, value: str) -> Path:
    relative = str(value or "").strip().replace("\\", "/")
    if not relative or Path(relative).is_absolute():
        raise MarketingQAApiError("asset path must be relative to the workspace")
    candidate = (root / relative).resolve()
    try:
        candidate.relative_to(root)
    except ValueError as exc:
        raise MarketingQAApiError("asset path escapes the workspace") from exc
    if not candidate.is_file():
        raise MarketingQAApiError(f"asset not found: {relative}", status=404)
    if candidate.suffix.lower() not in _IMAGE_EXTENSIONS:
        raise MarketingQAApiError("asset must be a supported image")
    return candidate


def _report_path(root: Path, report_id: str) -> Path:
    if not _REPORT_ID.fullmatch(report_id):
        raise MarketingQAApiError("invalid report id")
    path = _qa_dir(root) / f"{report_id}.json"
    if not path.is_file():
        raise MarketingQAApiError("visual QA report not found", status=404)
    return path


def _read_report(path: Path) -> dict[str, Any]:
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise MarketingQAApiError("visual QA report is unreadable", status=422) from exc
    if not isinstance(payload, dict):
        raise MarketingQAApiError("visual QA report is invalid", status=422)
    payload["id"] = path.stem
    payload.setdefault("effective_verdict", payload.get("verdict", "BLOCK"))
    return payload


def _report_summary(report: dict[str, Any]) -> dict[str, Any]:
    candidate = report.get("candidate") if isinstance(report.get("candidate"), dict) else {}
    scores = report.get("scores") if isinstance(report.get("scores"), dict) else {}
    findings = report.get("findings") if isinstance(report.get("findings"), list) else []
    return {
        "id": str(report.get("id") or ""),
        "created_at": report.get("created_at"),
        "candidate": {
            "path": candidate.get("path"),
            "width": candidate.get("width"),
            "height": candidate.get("height"),
        },
        "verdict": report.get("verdict", "BLOCK"),
        "effective_verdict": report.get("effective_verdict", report.get("verdict", "BLOCK")),
        "score": scores.get("overall", 0),
        "finding_count": len(findings),
        "human_override": report.get("human_override"),
    }


def marketing_qa_readiness(workspace_root: str | Path | None) -> dict[str, Any]:
    root = _require_workspace(workspace_root)
    from navin.config.loader import load_config
    from navin.providers.factory import load_provider_snapshot

    config = load_config().tools.visual_qa
    readiness = visual_qa_readiness(config, load_provider_snapshot)
    return {
        "schema_version": 1,
        "workspace": str(root),
        "ready": readiness["ready"],
        "dependency": {"name": "Pillow", "ready": readiness["dependency"]},
        "providers": [
            {
                "name": readiness["provider"],
                "adapter": readiness["adapter"],
                "credentials": readiness["credentials"],
                "model": readiness["model"],
                "ready": readiness["ready"],
                "error": readiness["error"],
            }
        ],
    }


def list_marketing_qa_assets(
    workspace_root: str | Path | None, *, limit: int = 500
) -> dict[str, Any]:
    root = _require_workspace(workspace_root)
    safe_limit = max(1, min(limit, 1000))
    assets: list[dict[str, Any]] = []
    for path in root.rglob("*"):
        relative = path.relative_to(root)
        if any(part in _SKIP_PARTS for part in relative.parts):
            continue
        if "marketing/qa" in relative.as_posix() or not path.is_file():
            continue
        if path.suffix.lower() not in _IMAGE_EXTENSIONS:
            continue
        stat = path.stat()
        assets.append(
            {
                "path": relative.as_posix(),
                "name": path.name,
                "size": stat.st_size,
                "updated_at": datetime.fromtimestamp(stat.st_mtime, UTC).isoformat(),
            }
        )
    assets.sort(key=lambda item: (str(item["updated_at"]), str(item["path"])), reverse=True)
    return {"count": len(assets), "assets": assets[:safe_limit]}


def list_marketing_qa_reports(
    workspace_root: str | Path | None, *, limit: int = 25
) -> dict[str, Any]:
    root = _require_workspace(workspace_root)
    safe_limit = max(1, min(limit, 100))
    reports: list[dict[str, Any]] = []
    paths = sorted(
        _qa_dir(root).glob("*.json") if _qa_dir(root).is_dir() else [],
        key=lambda item: item.stat().st_mtime,
        reverse=True,
    )
    for path in paths:
        if path.name == "human-overrides.json":
            continue
        try:
            reports.append(_report_summary(_read_report(path)))
        except MarketingQAApiError:
            continue
        if len(reports) >= safe_limit:
            break
    return {"count": len(reports), "reports": reports}


def get_marketing_qa_report(workspace_root: str | Path | None, report_id: str) -> dict[str, Any]:
    root = _require_workspace(workspace_root)
    return _read_report(_report_path(root, report_id))


async def run_marketing_qa(
    workspace_root: str | Path | None,
    *,
    candidate: str,
    references: list[str] | None = None,
    claims: list[str] | None = None,
    requirements: dict[str, Any] | None = None,
) -> dict[str, Any]:
    root = _require_workspace(workspace_root)
    candidate_path = _resolve_asset(root, candidate)
    reference_paths = [_resolve_asset(root, item) for item in references or []]

    from navin.config.loader import load_config
    from navin.providers.factory import load_provider_snapshot

    config = load_config().tools.visual_qa
    try:
        snapshot = await asyncio.to_thread(
            load_provider_snapshot, preset_name=config.preset or None
        )
        runtime = runtime_from_provider_snapshot(snapshot)
    except Exception:
        runtime = None
    factory = get_visual_qa_provider(config.provider)
    provider = (
        factory(runtime=runtime, provider_name=config.provider)
        if factory is not None and visual_qa_credentials_ready(runtime)
        else None
    )
    policy = VisualQAPolicy(
        pass_score=config.pass_score,
        warn_score=config.warn_score,
        min_sharpness=config.min_sharpness,
        min_contrast=config.min_contrast,
        block_on_vision_failure=config.block_on_vision_failure,
    )
    report = await run_visual_qa(
        candidate_path,
        references=reference_paths,
        claims=claims,
        requirements=requirements,
        provider=provider,
        policy=policy,
        workspace=root,
    )
    paths = write_visual_qa_report(report, root)
    report["reports"] = paths
    report["id"] = Path(paths["json"]).stem
    report["effective_verdict"] = report["verdict"]
    return report


def override_marketing_qa_report(
    workspace_root: str | Path | None,
    report_id: str,
    *,
    reason: str,
    verdict: str = "PASS",
) -> dict[str, Any]:
    root = _require_workspace(workspace_root)
    clean_reason = str(reason or "").strip()
    if len(clean_reason) < 3:
        raise MarketingQAApiError("override reason is required")
    clean_verdict = str(verdict or "").strip().upper()
    if clean_verdict not in _VERDICTS:
        raise MarketingQAApiError("override verdict must be PASS, WARN or BLOCK")
    path = _report_path(root, report_id)
    report = _read_report(path)
    now = datetime.now(UTC).isoformat()
    event = {
        "report_id": report_id,
        "created_at": now,
        "actor": "webui-human",
        "reason": clean_reason,
        "machine_verdict": report.get("verdict", "BLOCK"),
        "prior_effective_verdict": report.get("effective_verdict", report.get("verdict", "BLOCK")),
        "verdict": clean_verdict,
    }
    overrides = report.get("overrides")
    report["overrides"] = [*overrides, event] if isinstance(overrides, list) else [event]
    report["human_override"] = event
    report["effective_verdict"] = clean_verdict
    report.pop("id", None)
    atomic_write_text(path, json.dumps(report, ensure_ascii=False, indent=2, sort_keys=True) + "\n")

    audit_path = _qa_dir(root) / "human-overrides.json"
    audit: list[dict[str, Any]] = []
    if audit_path.is_file():
        try:
            existing = json.loads(audit_path.read_text(encoding="utf-8"))
            if isinstance(existing, list):
                audit = existing
        except (OSError, json.JSONDecodeError):
            audit = []
    atomic_write_text(
        audit_path,
        json.dumps([*audit, event], ensure_ascii=False, indent=2, sort_keys=True) + "\n",
    )
    return _read_report(path)
