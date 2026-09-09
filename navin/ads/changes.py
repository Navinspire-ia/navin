"""Approval-gated change store plus bulk exports for the platform editors.

Changes are proposed by the rules, approved (or rejected) by the user, then
either exported as an Ads Editor / bulk CSV or executed through a connected
MCP server. The store keeps one JSON line per change id (latest state wins)
and is rewritten atomically.
"""

from __future__ import annotations

import csv
import io
import json
import os
import tempfile
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterable

from filelock import FileLock

from navin.ads.models import ChangeStatus, Platform, ProposedChange

_TERMINAL = {"approved", "rejected", "applied"}
_MATCH_LABELS = {"exact": "Exact", "phrase": "Phrase", "broad": "Broad"}


class ChangeStore:
    def __init__(self, path: str | Path) -> None:
        self.path = Path(path)
        self.lock = FileLock(str(self.path) + ".lock")

    # -- persistence ---------------------------------------------------------

    def _read(self) -> dict[str, dict[str, Any]]:
        if not self.path.exists():
            return {}
        out: dict[str, dict[str, Any]] = {}
        for line in self.path.read_text(encoding="utf-8").splitlines():
            line = line.strip()
            if not line:
                continue
            try:
                record = json.loads(line)
            except json.JSONDecodeError:
                continue
            if isinstance(record, dict) and record.get("id"):
                out[str(record["id"])] = record
        return out

    def _write(self, records: dict[str, dict[str, Any]]) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        body = "".join(
            json.dumps(records[key], ensure_ascii=False, sort_keys=True, separators=(",", ":")) + "\n"
            for key in sorted(records)
        )
        fd, temp_name = tempfile.mkstemp(prefix=self.path.name + ".", dir=self.path.parent)
        try:
            with os.fdopen(fd, "w", encoding="utf-8", newline="\n") as handle:
                handle.write(body)
                handle.flush()
                os.fsync(handle.fileno())
            os.replace(temp_name, self.path)
        finally:
            if os.path.exists(temp_name):
                os.unlink(temp_name)

    # -- API -----------------------------------------------------------------

    def upsert_proposals(self, changes: Iterable[ProposedChange]) -> dict[str, int]:
        """Record fresh proposals; decisions already taken are never overwritten."""
        added = refreshed = kept = 0
        now = _now()
        with self.lock:
            records = self._read()
            for change in changes:
                existing = records.get(change.id)
                payload = change.model_dump(mode="json")
                if existing is None:
                    payload["proposed_at"] = now
                    payload["updated_at"] = now
                    records[change.id] = payload
                    added += 1
                elif existing.get("status") in _TERMINAL:
                    kept += 1
                else:
                    payload["proposed_at"] = existing.get("proposed_at", now)
                    payload["updated_at"] = now
                    records[change.id] = payload
                    refreshed += 1
            self._write(records)
        return {"added": added, "refreshed": refreshed, "kept": kept, "total": len(records)}

    def list(
        self,
        *,
        status: str | None = None,
        platform: str | None = None,
        ids: Iterable[str] | None = None,
    ) -> list[dict[str, Any]]:
        wanted = set(ids) if ids is not None else None
        rows = []
        for record in self._read().values():
            if status and record.get("status") != status:
                continue
            if platform and record.get("platform") != platform:
                continue
            if wanted is not None and record.get("id") not in wanted:
                continue
            rows.append(record)
        rows.sort(key=lambda item: (
            {"proposed": 0, "approved": 1, "applied": 2, "rejected": 3}.get(str(item.get("status")), 9),
            -float(item.get("estimated_monthly_savings") or 0.0),
            str(item.get("id")),
        ))
        return rows

    def set_status(self, ids: Iterable[str], status: ChangeStatus, *, note: str = "") -> dict[str, Any]:
        wanted = [str(item).strip() for item in ids if str(item).strip()]
        if status not in {"proposed", "approved", "rejected", "applied"}:
            raise ValueError("status must be proposed, approved, rejected or applied")
        updated: list[str] = []
        missing: list[str] = []
        now = _now()
        with self.lock:
            records = self._read()
            for change_id in wanted:
                record = records.get(change_id)
                if record is None:
                    missing.append(change_id)
                    continue
                if status == "applied" and record.get("status") != "approved":
                    raise ValueError(f"{change_id} must be approved before it is marked applied")
                record["status"] = status
                record["updated_at"] = now
                record[f"{status}_at"] = now
                if note:
                    record["note"] = note
                updated.append(change_id)
            self._write(records)
        return {"updated": updated, "missing": missing, "status": status}

    def export(
        self,
        *,
        platform: str | None = None,
        status: str = "approved",
        format: str = "csv",  # noqa: A002
        ids: Iterable[str] | None = None,
    ) -> tuple[str, str]:
        """Return ``(suggested_filename, csv_text)`` for approved changes."""
        rows = self.list(status=status, platform=platform, ids=ids)
        if format == "csv":
            return f"ads-changes-{platform or 'all'}.csv", generic_csv(rows)
        if format == "google_editor":
            return "google-ads-editor-import.csv", google_editor_csv(rows)
        if format == "microsoft_bulk":
            return "microsoft-ads-bulk.csv", microsoft_bulk_csv(rows)
        raise ValueError("format must be csv, google_editor or microsoft_bulk")


def _now() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat()


# --- exports -----------------------------------------------------------------


def generic_csv(rows: list[dict[str, Any]]) -> str:
    buffer = io.StringIO()
    writer = csv.writer(buffer, lineterminator="\n")
    writer.writerow([
        "Change id", "Status", "Platform", "Action", "Level", "Campaign", "Ad group", "Ad", "Keyword",
        "Match type", "Budget change (%)", "Bid change (%)", "Estimated monthly savings", "Rationale",
    ])
    for row in rows:
        target = row.get("target") or {}
        params = row.get("params") or {}
        writer.writerow([
            row.get("id", ""), row.get("status", ""), row.get("platform", ""), row.get("action", ""),
            row.get("level", ""), target.get("campaign", ""), target.get("ad_group", ""), target.get("ad", ""),
            params.get("keyword") or target.get("keyword", ""), params.get("match_type") or target.get("match_type", ""),
            params.get("budget_change_pct", ""), params.get("bid_change_pct", ""),
            row.get("estimated_monthly_savings", ""), row.get("rationale", ""),
        ])
    return buffer.getvalue()


def google_editor_csv(rows: list[dict[str, Any]]) -> str:
    """Google Ads Editor import sheet: negatives + status changes only."""
    buffer = io.StringIO()
    writer = csv.writer(buffer, lineterminator="\n")
    writer.writerow(["Campaign", "Ad group", "Keyword", "Criterion Type", "Campaign status", "Ad group status", "Status", "Comment"])
    for row in rows:
        if row.get("platform") not in {Platform.GOOGLE.value, Platform.UNKNOWN.value}:
            continue
        target = row.get("target") or {}
        params = row.get("params") or {}
        action = row.get("action")
        campaign = target.get("campaign", "")
        ad_group = target.get("ad_group", "")
        comment = f"navin {row.get('id', '')}"
        if action == "add_negative_keyword":
            match = _MATCH_LABELS.get(str(params.get("match_type", "exact")).lower(), "Exact")
            writer.writerow([campaign, ad_group, params.get("keyword", ""), f"Negative {match}", "", "", "", comment])
        elif action == "pause":
            level = row.get("level")
            if level == "campaign":
                writer.writerow([campaign, "", "", "", "Paused", "", "", comment])
            elif level == "ad_group":
                writer.writerow([campaign, ad_group, "", "", "", "Paused", "", comment])
            elif level == "keyword":
                match = _MATCH_LABELS.get(str(target.get("match_type", "")).lower(), "")
                writer.writerow([campaign, ad_group, target.get("keyword", ""), match, "", "", "Paused", comment])
    return buffer.getvalue()


def microsoft_bulk_csv(rows: list[dict[str, Any]]) -> str:
    """Microsoft Advertising bulk sheet (Type / Status / Campaign / Ad Group / Keyword / Match Type)."""
    buffer = io.StringIO()
    writer = csv.writer(buffer, lineterminator="\n")
    writer.writerow(["Type", "Status", "Campaign", "Ad Group", "Keyword", "Match Type", "Text", "Comment"])
    for row in rows:
        if row.get("platform") not in {Platform.MICROSOFT.value, Platform.UNKNOWN.value}:
            continue
        target = row.get("target") or {}
        params = row.get("params") or {}
        action = row.get("action")
        campaign = target.get("campaign", "")
        ad_group = target.get("ad_group", "")
        comment = f"navin {row.get('id', '')}"
        if action == "add_negative_keyword":
            match = _MATCH_LABELS.get(str(params.get("match_type", "exact")).lower(), "Exact")
            kind = "Ad Group Negative Keyword" if ad_group else "Campaign Negative Keyword"
            writer.writerow([kind, "Active", campaign, ad_group, "", match, params.get("keyword", ""), comment])
        elif action == "pause":
            level = row.get("level")
            if level == "campaign":
                writer.writerow(["Campaign", "Paused", campaign, "", "", "", "", comment])
            elif level == "ad_group":
                writer.writerow(["Ad Group", "Paused", campaign, ad_group, "", "", "", comment])
            elif level == "keyword":
                match = _MATCH_LABELS.get(str(target.get("match_type", "")).lower(), "")
                writer.writerow(["Keyword", "Paused", campaign, ad_group, target.get("keyword", ""), match, "", comment])
    return buffer.getvalue()


def mcp_execution_plan(rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Neutral steps the agent maps onto the connected MCP's write tools.

    Only approved changes are listed; each step says what to read back to
    verify the mutation, so nothing is applied blind.
    """
    plan: list[dict[str, Any]] = []
    for row in rows:
        if row.get("status") != "approved":
            continue
        target = row.get("target") or {}
        params = row.get("params") or {}
        action = row.get("action")
        platform = row.get("platform", "unknown")
        server = _MCP_SERVER.get(str(platform), "the platform MCP")
        step: dict[str, Any] = {
            "change_id": row.get("id"), "platform": platform, "mcp_server": server, "action": action,
            "target": target, "params": params,
        }
        if action == "add_negative_keyword":
            step["operation"] = (
                f"Create a negative keyword '{params.get('keyword', '')}' ({params.get('match_type', 'exact')}) "
                f"at {params.get('scope', 'campaign')} scope on {_target_label(target)}."
            )
            step["verify"] = "List the negative keywords of the target and confirm the term is present."
        elif action == "pause":
            step["operation"] = f"Set status PAUSED on {_target_label(target)}."
            step["verify"] = "Read the entity back and confirm status is PAUSED."
        elif action in {"reduce_budget", "increase_budget"}:
            step["operation"] = (
                f"Read the current daily budget of {_target_label(target)}, then set it to "
                f"current * (1 + {params.get('budget_change_pct', 0)}/100)."
            )
            step["verify"] = "Read the budget back and confirm the new amount."
        elif action == "lower_bid":
            step["operation"] = f"Lower the bid of {_target_label(target)} by {abs(float(params.get('bid_change_pct', 0)))}%."
            step["verify"] = "Read the bid back."
        elif action == "rebalance_budget":
            step["operation"] = "Shift budget from the flagged campaign to converting ones; amounts per the approved params."
            step["verify"] = "Read the budgets of all touched campaigns."
        else:
            step["operation"] = f"Manual follow-up ({action}) on {_target_label(target)}; nothing to mutate through MCP."
            step["verify"] = "n/a"
        plan.append(step)
    return plan


_MCP_SERVER = {
    Platform.GOOGLE.value: "google-ads",
    Platform.MICROSOFT.value: "microsoft-ads",
    Platform.META.value: "meta-ads",
    Platform.LINKEDIN.value: "linkedin-ads",
    Platform.TIKTOK.value: "tiktok-ads",
    Platform.REDDIT.value: "reddit-ads",
}


def _target_label(target: dict[str, str]) -> str:
    return " > ".join(value for key, value in target.items() if value and key in {"campaign", "ad_group", "ad", "keyword"}) or "the account"
