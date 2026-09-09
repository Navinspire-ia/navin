# Copyright (c) 2026-present Navinspire IA
# SPDX-License-Identifier: AGPL-3.0-only

"""Deal / contact health shared by the UI and the ``crm`` tool."""

from __future__ import annotations

import json
import re
import time
from pathlib import Path
from typing import Any

from navin.crm.store import get_record, list_audit, list_records

_KIND_LABEL = {
    "contacts": "Contact",
    "companies": "Entreprise",
    "leads": "Lead",
    "opportunities": "Opportunite",
    "activities": "Activite",
    "products": "Produit",
}
_ID_RE = re.compile(r"^[a-z]{1,6}-[a-f0-9]{6,}$", re.I)


def _human_audit_name(detail: str) -> str:
    text = str(detail or "").strip()
    if not text or text in {"null", "undefined"}:
        return ""
    if text.startswith("{") or text.startswith("["):
        try:
            parsed = json.loads(text)
        except json.JSONDecodeError:
            return ""
        if not isinstance(parsed, dict):
            return ""
        nested = parsed.get("after") if isinstance(parsed.get("after"), dict) else parsed
        first = str(nested.get("firstName") or "").strip()
        last = str(nested.get("lastName") or "").strip()
        full = f"{first} {last}".strip()
        name = str(nested.get("name") or parsed.get("name") or "").strip()
        title = str(nested.get("title") or "").strip()
        for value in (name, full, title):
            if value and value not in {"null", "undefined"}:
                return value
        return ""
    if _ID_RE.match(text) or text.upper().startswith("AUDIT "):
        return ""
    return text


def _human_audit_title(action: str, kind: str, detail: str) -> str:
    noun = _KIND_LABEL.get(kind, kind.replace("_", " ").strip() or "Fiche")
    name = _human_audit_name(detail)
    verbs = {
        "create": f"{noun} cree",
        "update": f"{noun} modifie",
        "delete": f"{noun} supprime",
        "stage": "Etape changee",
        "convert": "Lead converti",
        "invite": "Invitation",
        "accept": "Invitation acceptee",
        "decline": "Invitation refusee",
        "role": "Role mis a jour",
        "kick": "Membre retire",
        "settings": "Parametres enregistres",
    }
    title = verbs.get(action, f"{noun} {action}".strip())
    return f"{title} - {name}" if name else title


def insights(project: Path, kind: str, record_id: str) -> dict[str, Any]:
    row = get_record(project, kind, record_id)
    now = int(time.time())
    activities = [
        item
        for item in list_records(project, "activities")
        if _linked(item, kind, record_id)
    ]
    last = max((int(item.get("at") or 0) for item in activities), default=0)
    days = (now - last) // 86400 if last else 99
    health = 88
    if days > 3:
        health -= min(40, (days - 3) * 5)
    if kind == "opportunities":
        if int(row.get("probability") or 0) < 30:
            health -= 10
        if not str(row.get("nextAction") or "").strip():
            health -= 8
    health = max(12, min(100, health))
    tone = "good" if health >= 70 else "watch" if health >= 45 else "risk"
    next_action = str(row.get("nextAction") or "").strip()
    if not next_action:
        next_action = "Relancer avant la fin de la semaine" if days >= 3 else "Tenir le prochain point"
    risk = "Aucun rendez-vous programme" if days >= 7 else ""
    return {
        "id": row.get("id"),
        "kind": kind,
        "health": health,
        "tone": tone,
        "lastContactDays": days if last else None,
        "nextAction": next_action,
        "risk": risk,
        "suggestion": "Relancer avant jeudi" if days >= 3 else "Continuer le fil actuel",
        "activityCount": len(activities),
    }


def _linked(activity: dict[str, Any], kind: str, record_id: str) -> bool:
    if kind == "contacts":
        return str(activity.get("contactId") or "") == record_id
    if kind == "companies":
        return str(activity.get("companyId") or "") == record_id
    if kind == "opportunities":
        return str(activity.get("opportunityId") or "") == record_id
    if kind == "leads":
        return str(activity.get("leadId") or "") == record_id
    return False


def timeline(project: Path, kind: str, record_id: str) -> list[dict[str, Any]]:
    get_record(project, kind, record_id)
    rows = [
        item
        for item in list_records(project, "activities")
        if _linked(item, kind, record_id)
    ]
    history = list_audit(project, kind=kind, record_id=record_id, limit=40)
    for item in history:
        rows.append(
            {
                "id": item["id"],
                "kind": "audit",
                "title": _human_audit_title(item.get("action") or "", item.get("kind") or kind, item.get("detail") or ""),
                "body": _human_audit_name(item.get("detail") or ""),
                "at": item.get("createdAt") or 0,
                "actor": item.get("actor") or "",
                "action": item.get("action") or "",
                "auditKind": item.get("kind") or kind,
                "detail": item.get("detail") or "",
                "audit": True,
            }
        )
    rows.sort(key=lambda item: int(item.get("at") or item.get("createdAt") or 0), reverse=True)
    return rows[:80]
