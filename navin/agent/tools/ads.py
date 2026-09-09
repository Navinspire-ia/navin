"""Paid media engine tool, intended for the Ads product module."""

from __future__ import annotations

import json
import re
from pathlib import Path
from typing import Any

from pydantic import Field

from navin.ads.changes import ChangeStore, mcp_execution_plan
from navin.ads.ingest import IngestResult, ingest_file, ingest_text, parse_records
from navin.ads.models import AdRow, AdsAnalysis
from navin.ads.report import report_payload, summary_lines, write_report
from navin.ads.rules import AdsThresholds, analyze_rows
from navin.ads.scoring import health_score
from navin.agent.tools.base import Tool, ToolResult, tool_parameters
from navin.agent.tools.path_utils import project_rooted_path, resolve_workspace_path
from navin.agent.tools.schema import NumberSchema, StringSchema, tool_parameters_schema
from navin.config_base import Base
from navin.security.workspace_access import current_tool_workspace
from navin.security.workspace_policy import WorkspaceBoundaryError, resolve_allowed_path

_PLATFORMS = ["auto", "google", "microsoft", "meta", "linkedin", "tiktok", "reddit"]
_ENTITY_LIMIT = 30
_ROW_PREVIEW = 40


class AdsToolConfig(Base):
    enabled: bool = True
    currency: str = ""
    changes_store_path: str = "ads/changes.jsonl"
    max_rows: int = Field(default=200_000, ge=100, le=5_000_000)
    thresholds: AdsThresholds = Field(default_factory=AdsThresholds)


@tool_parameters(
    tool_parameters_schema(
        action=StringSchema(
            "Ads action.",
            enum=["ingest", "analyze", "score", "report", "changes", "export_changes", "pipeline"],
        ),
        paths=StringSchema(
            "Export files (CSV / TSV / XLSX from Google, Microsoft, Meta, LinkedIn, TikTok, Reddit "
            "Ads Manager), comma or newline separated. Chat attachments paths work as-is."
        ),
        data=StringSchema(
            "Inline data: CSV text, a JSON list of rows (MCP report output, API dumps), "
            "or a previous analysis payload for score / report."
        ),
        platform=StringSchema("Platform of the export (auto-detected when omitted).", enum=_PLATFORMS),
        monthly_budget=NumberSchema(description="Monthly budget for pacing checks.", minimum=0),
        currency=StringSchema("Account currency code shown in reports, for example EUR."),
        format=StringSchema(
            "Report format (json, md, html) or change export format (csv, google_editor, microsoft_bulk).",
            enum=["json", "md", "html", "csv", "google_editor", "microsoft_bulk"],
        ),
        path=StringSchema("Workspace-relative output path (report or change export)."),
        ids=StringSchema("Change ids, comma separated (changes action)."),
        status=StringSchema(
            "changes action: filter to list, or new status to set when ids are given.",
            enum=["proposed", "approved", "rejected", "applied"],
        ),
        note=StringSchema("Note stored with a status change."),
        required=["action"],
    )
)
class AdsTool(Tool):
    """Grounded paid-media engine: exports in, metrics, findings, approval-gated changes out."""

    config_key = "ads"
    _scopes = {"core"}
    product_module = "ads"

    @classmethod
    def config_cls(cls):
        return AdsToolConfig

    @classmethod
    def enabled(cls, ctx: Any) -> bool:
        return ctx.config.ads.enabled

    @classmethod
    def create(cls, ctx: Any) -> Tool:
        return cls(workspace=ctx.workspace, config=ctx.config.ads)

    def __init__(self, *, workspace: str | Path, config: AdsToolConfig) -> None:
        self.workspace = Path(workspace).expanduser()
        self.config = config

    @property
    def name(self) -> str:
        return "ads"

    @property
    def description(self) -> str:
        return (
            "Ads-only engine for real campaign analysis. Ingests Ads Manager exports (CSV/XLSX) "
            "or MCP report rows for Google, Microsoft, Meta, LinkedIn, TikTok and Reddit Ads, "
            "computes CTR/CPC/CPA/ROAS per campaign, ad group, ad, keyword and search term, "
            "flags wasted spend, high CPA, low CTR, creative fatigue, quality score, pacing, and "
            "proposes approval-gated changes (pause, budget, negatives) with bulk exports for the "
            "platform editors. Never invent spend, conversions or ROAS: use pipeline with real "
            "exports, or report the data_gap."
        )

    @property
    def read_only(self) -> bool:
        return False

    # -- paths ---------------------------------------------------------------

    def _access(self):
        return current_tool_workspace(self.workspace, restrict_to_workspace=True)

    def _input_path(self, raw: str) -> Path:
        access = self._access()
        workspace = access.project_path or self.workspace
        try:
            resolved = resolve_workspace_path(
                raw, workspace=workspace, allowed_dir=access.allowed_root, include_media_dir=True,
            )
        except WorkspaceBoundaryError as exc:
            raise ValueError("export paths must stay inside the project or the chat attachments") from exc
        path = Path(resolved)
        if not path.is_file():
            raise ValueError(f"export not found: {raw}")
        return path

    def _output_path(self, raw: str) -> Path:
        access = self._access()
        workspace = access.project_path or self.workspace
        try:
            resolved = resolve_allowed_path(
                project_rooted_path(raw, workspace, [access.allowed_root]),
                workspace=workspace,
                allowed_root=access.allowed_root,
                strict=False,
            )
        except WorkspaceBoundaryError as exc:
            raise ValueError("path must stay inside the project") from exc
        return Path(resolved)

    def _store(self) -> ChangeStore:
        return ChangeStore(self._output_path(self.config.changes_store_path))

    # -- inputs --------------------------------------------------------------

    @staticmethod
    def _split_paths(paths: str | None) -> list[str]:
        if not paths:
            return []
        return [item.strip().strip('"').strip("'") for item in re.split(r"[\n,;]+", paths) if item.strip()]

    def _ingest_all(self, paths: str | None, data: str | None, platform: str | None) -> list[IngestResult]:
        chosen = None if not platform or platform == "auto" else platform
        results: list[IngestResult] = []
        for raw in self._split_paths(paths):
            results.append(ingest_file(self._input_path(raw), platform=chosen))
        if data and data.strip():
            text = data.strip()
            if text.startswith(("[", "{")):
                payload = json.loads(text)
                if isinstance(payload, dict) and "schema_version" in payload and "findings" in payload:
                    raise ValueError("data is an analysis payload; use action=score or action=report with it")
                records = payload.get("rows", payload.get("results", payload.get("data"))) if isinstance(payload, dict) else payload
                if not isinstance(records, list):
                    raise ValueError("JSON data must be a list of rows or an object with a rows list")
                results.append(parse_records(records, platform=chosen, source="inline-json"))
            else:
                results.append(ingest_text(text, platform=chosen, source="inline-csv"))
        if not results:
            raise ValueError("paths or data is required")
        total = sum(len(item.rows) for item in results)
        if total > self.config.max_rows:
            raise ValueError(f"too many rows ({total} > {self.config.max_rows}); aggregate the export first")
        if total == 0:
            raise ValueError("no data rows parsed from the export(s)")
        return results

    def _analysis_from(
        self,
        results: list[IngestResult],
        *,
        monthly_budget: float | None,
        currency: str | None,
    ) -> AdsAnalysis:
        rows: list[AdRow] = [row for result in results for row in result.rows]
        gaps: list[dict[str, str]] = []
        for result in results:
            gaps.extend(result.data_gaps)
            gaps.extend({"service": f"export:{result.source}", "reason": warning} for warning in result.warnings)
        return analyze_rows(
            rows,
            thresholds=self.config.thresholds,
            monthly_budget=monthly_budget,
            currency=(currency or self.config.currency or "").upper(),
            sources=[result.source for result in results],
            columns={result.source: result.columns for result in results},
            data_gaps=gaps,
        )

    @staticmethod
    def _analysis_from_payload(data: str | None) -> AdsAnalysis:
        if not data:
            raise ValueError("data (analysis payload) is required")
        payload = json.loads(data)
        if not isinstance(payload, dict) or "findings" not in payload:
            raise ValueError("data must be an analysis payload from action=analyze or pipeline")
        payload = dict(payload)
        payload.pop("health", None)
        payload.pop("summary", None)
        payload.pop("path", None)
        payload.pop("changes_store", None)
        payload.pop("next_step", None)
        entities = payload.get("entities")
        if isinstance(entities, dict):
            # Accept the compact shape returned by analyze / pipeline.
            payload["entities"] = {
                level: (items.get("top_by_spend", []) if isinstance(items, dict) else items)
                for level, items in entities.items()
            }
        return AdsAnalysis.model_validate(payload)

    # -- execute -------------------------------------------------------------

    async def execute(
        self,
        action: str,
        paths: str | None = None,
        data: str | None = None,
        platform: str | None = None,
        monthly_budget: float | None = None,
        currency: str | None = None,
        format: str | None = None,  # noqa: A002
        path: str | None = None,
        ids: str | None = None,
        status: str | None = None,
        note: str | None = None,
        **_: Any,
    ) -> str:
        try:
            action = action.strip().lower()
            if action == "ingest":
                results = self._ingest_all(paths, data, platform)
                return self._json({"exports": [self._ingest_summary(item) for item in results]})
            if action == "analyze":
                results = self._ingest_all(paths, data, platform)
                analysis = self._analysis_from(results, monthly_budget=monthly_budget, currency=currency)
                return self._json(self._compact(report_payload(analysis)))
            if action == "score":
                analysis = self._analysis_from_payload(data)
                return self._json(health_score(analysis))
            if action == "report":
                if not path:
                    raise ValueError("path is required")
                if data and data.strip().startswith("{") and "findings" in data:
                    analysis = self._analysis_from_payload(data)
                else:
                    results = self._ingest_all(paths, data, platform)
                    analysis = self._analysis_from(results, monthly_budget=monthly_budget, currency=currency)
                output = write_report(analysis, self._output_path(path), format or "html")
                return self._json({
                    "path": str(output), "health": health_score(analysis), "summary": summary_lines(analysis),
                })
            if action == "changes":
                return self._json(self._changes(ids=ids, status=status, note=note, platform=platform))
            if action == "export_changes":
                return self._json(self._export_changes(platform=platform, format=format, path=path, ids=ids))
            if action == "pipeline":
                results = self._ingest_all(paths, data, platform)
                analysis = self._analysis_from(results, monthly_budget=monthly_budget, currency=currency)
                fmt = format if format in {"json", "md", "html"} else "html"
                output = self._output_path(path or f"ads/ads-report.{fmt}")
                write_report(analysis, output, fmt)
                stored = self._store().upsert_proposals(analysis.changes)
                payload = self._compact(report_payload(analysis))
                payload["path"] = str(output)
                payload["changes_store"] = {**stored, "path": str(self._output_path(self.config.changes_store_path))}
                payload["next_step"] = (
                    "Review the proposed changes with the user; approve with action=changes "
                    "status=approved ids=..., then action=export_changes (csv / google_editor / "
                    "microsoft_bulk) or apply the approved ones through the connected MCP."
                )
                return self._json(payload)
            raise ValueError("unknown Ads action")
        except ValueError as exc:
            return ToolResult.error(f"Error: {exc}")
        except Exception as exc:  # noqa: BLE001
            return ToolResult.error(f"Error: Ads action failed: {exc}")

    # -- helpers -------------------------------------------------------------

    def _changes(self, *, ids: str | None, status: str | None, note: str | None, platform: str | None) -> dict[str, Any]:
        store = self._store()
        wanted = [item.strip() for item in (ids or "").split(",") if item.strip()]
        chosen = None if not platform or platform == "auto" else platform
        if wanted and status:
            result = store.set_status(wanted, status, note=note or "")  # type: ignore[arg-type]
            rows = store.list(ids=wanted)
            out: dict[str, Any] = {**result, "changes": rows}
            if status == "approved":
                out["mcp_plan"] = mcp_execution_plan(rows)
            return out
        rows = store.list(status=status, platform=chosen, ids=wanted or None)
        out = {
            "count": len(rows),
            "by_status": _count_by(rows, "status"),
            "changes": rows,
        }
        approved = [row for row in rows if row.get("status") == "approved"]
        if approved:
            out["mcp_plan"] = mcp_execution_plan(approved)
        return out

    def _export_changes(
        self, *, platform: str | None, format: str | None, path: str | None, ids: str | None
    ) -> dict[str, Any]:
        fmt = format if format in {"csv", "google_editor", "microsoft_bulk"} else "csv"
        chosen = None if not platform or platform == "auto" else platform
        if fmt == "google_editor":
            chosen = chosen or "google"
        if fmt == "microsoft_bulk":
            chosen = chosen or "microsoft"
        wanted = [item.strip() for item in (ids or "").split(",") if item.strip()] or None
        filename, text = self._store().export(platform=chosen, format=fmt, ids=wanted)
        output = self._output_path(path or f"ads/{filename}")
        output.parent.mkdir(parents=True, exist_ok=True)
        output.write_text(text, encoding="utf-8", newline="\n")
        line_count = max(0, text.count("\n") - 1)
        return {
            "path": str(output), "format": fmt, "rows": line_count,
            "note": (
                "Only approved changes are exported. Import the file in the platform editor "
                "(Google Ads Editor / Microsoft Advertising Editor) and review before posting."
            ),
        }

    @staticmethod
    def _ingest_summary(result: IngestResult) -> dict[str, Any]:
        return {
            "source": result.source,
            "platform": result.platform.value,
            "rows": len(result.rows),
            "levels": sorted({row.level().value for row in result.rows}),
            "columns": result.columns,
            "unknown_columns": result.unknown_columns,
            "warnings": result.warnings,
            "data_gaps": result.data_gaps,
            "preview": [row.model_dump(mode="json", exclude_defaults=True) for row in result.rows[:_ROW_PREVIEW]],
        }

    @staticmethod
    def _compact(payload: dict[str, Any]) -> dict[str, Any]:
        """Keep tool output readable: top entities per level, no raw row dump."""
        entities = payload.get("entities") or {}
        trimmed: dict[str, Any] = {}
        for level, items in entities.items():
            trimmed[level] = {
                "count": len(items),
                "top_by_spend": items[:_ENTITY_LIMIT],
            }
        payload["entities"] = trimmed
        return payload

    @staticmethod
    def _json(payload: Any) -> str:
        return json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True)


def _count_by(rows: list[dict[str, Any]], key: str) -> dict[str, int]:
    out: dict[str, int] = {}
    for row in rows:
        value = str(row.get(key, ""))
        out[value] = out.get(value, 0) + 1
    return dict(sorted(out.items()))
