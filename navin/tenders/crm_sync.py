"""Push qualified notices into the project CRM as buyers and opportunities."""

from __future__ import annotations

import os
from pathlib import Path
from typing import Any

from navin.tenders.desk import in_play
from navin.tenders.errors import TenderError
from navin.tenders.normalize import looks_like_notice
from navin.tenders.store import TenderStore

# Tender stage -> CRM opportunity stage. Anything not listed stays out of the CRM.
STAGE_MAP = {
    "go": "qualifie",
    "drafting": "proposition",
    "validating": "proposition",
    "submitted": "negociation",
    "clarification": "negociation",
    "shortlisted": "negociation",
    "negotiation": "negociation",
    "won": "gagne",
    "lost": "perdu",
}


def project_root() -> Path:
    try:
        from navin.security.workspace_access import current_tool_workspace

        access = current_tool_workspace(None, restrict_to_workspace=False)
        raw = str(getattr(access, "project_path", "") or "").strip()
    except Exception:
        raw = ""
    if not raw:
        for key in ("NAVIN_PROJECT", "NAVIN_WORKSPACE"):
            raw = str(os.environ.get(key) or "").strip()
            if raw:
                break
    if not raw:
        raise TenderError(
            "no project folder for this session. Open a project to sync the CRM.",
            status=400,
        )
    root = Path(raw).expanduser()
    if not root.is_dir():
        raise TenderError("project root not found", status=404)
    return root.resolve()


def crm_targets(rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """A no-go is not a deal. Only what you decided to chase reaches the CRM."""
    out = []
    for row in rows:
        if not looks_like_notice(row):
            continue
        stage = str(row.get("stage") or "")
        if stage not in STAGE_MAP:
            continue
        if stage == "go" and not in_play(row):
            continue
        out.append(row)
    return out


def _buyer_company(root: Path, row: dict[str, Any], actor: str) -> str:
    from navin.crm.store import create_record, list_records

    name = str(row.get("buyer") or "").strip()
    if not name:
        return ""
    wanted = name.casefold()
    for item in list_records(root, "companies"):
        if str(item.get("name") or "").strip().casefold() == wanted:
            return str(item.get("id") or "")
    created = create_record(
        root,
        "companies",
        {
            "name": name[:120],
            "country": str(row.get("country") or "")[:80],
            "industry": "Public sector",
            "tags": ["tenders"],
            "notes": f"Contracting authority imported from Navin Tenders ({row.get('source_id') or 'source'}).",
        },
        actor=actor,
    )
    return str(created.get("id") or "")


def _opportunity_body(row: dict[str, Any], company_id: str, profile: dict[str, Any]) -> dict[str, Any]:
    score = row.get("score")
    budget = row.get("budget")
    title = str(row.get("title") or "Tender")[:160]
    reference = str(row.get("reference") or "").strip()
    return {
        "name": f"AO {title}"[:160],
        "amount": float(budget) if isinstance(budget, (int, float)) else 0.0,
        "currency": str(profile.get("currency") or "EUR"),
        "probability": int(round(float(score))) if isinstance(score, (int, float)) else 0,
        "stage": STAGE_MAP[str(row.get("stage") or "go")],
        "closeDate": str(row.get("deadline") or "")[:20],
        "companyId": company_id,
        "source": "tenders",
        "nextAction": str(row.get("go_reason") or "")[:200],
        "notes": "\n".join(
            part
            for part in (
                f"Reference: {reference}" if reference else "",
                f"Notice: {row.get('source_url') or ''}",
                f"Navin score: {score}/100" if score is not None else "",
            )
            if part
        )[:4000],
    }


def sync_crm(store: TenderStore, tender_ids: list[str] | None = None, actor: str = "") -> dict[str, Any]:
    from navin.crm.store import create_record, get_record, new_id, update_record

    profile = store.load_profile()
    rows = crm_targets(store.load_tenders())
    wanted = {str(tid).strip() for tid in (tender_ids or []) if str(tid).strip()}
    if wanted:
        rows = [row for row in rows if str(row.get("id")) in wanted]
        if not rows:
            raise TenderError(
                "this notice is not a deal yet. Score a GO or draft a reply first.",
                status=409,
            )
    if not rows:
        return {"created": 0, "updated": 0, "pushed": [], "total": 0}
    root = project_root()
    created = 0
    updated = 0
    pushed: list[dict[str, Any]] = []
    for row in rows:
        tid = str(row.get("id"))
        company_id = _buyer_company(root, row, actor)
        body = _opportunity_body(row, company_id, profile)
        opp_id = str(row.get("crm_opportunity_id") or "") or new_id("opportunities", f"tender:{tid}")
        try:
            get_record(root, "opportunities", opp_id)
            record = update_record(root, "opportunities", opp_id, body, actor=actor)
            updated += 1
        except Exception:
            record = create_record(root, "opportunities", {**body, "id": opp_id}, actor=actor)
            created += 1
        store.patch(tid, {"crm_opportunity_id": str(record.get("id") or opp_id)})
        pushed.append({"id": tid, "opportunity_id": record.get("id"), "stage": record.get("stage")})
    if pushed:
        store.append_journal(
            {"kind": "crm", "text": f"{created} created, {updated} updated in the project CRM"}
        )
    return {"created": created, "updated": updated, "pushed": pushed, "total": len(pushed)}
