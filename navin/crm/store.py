"""SQLite WAL store for the CRM workbench and the ``crm`` agent tool."""

from __future__ import annotations

import json
import sqlite3
import time
import uuid
from pathlib import Path
from typing import Any

from navin.crm.db import connect, crm_root, project_lock, sqlite_path, wal_status
from navin.crm.schema import (
    ACTIVITY_KINDS,
    CONTACT_STATUSES,
    FOLLOWUP_DEFAULT_DAYS,
    INVITE_STATUSES,
    KINDS,
    LEAD_STAGES,
    LOST_STAGE,
    OPEN_OPP_STAGES,
    OPP_STAGES,
    ROLE_RANK,
    ROLES,
    WON_STAGE,
)

_PREFIX = {
    "companies": "cmp",
    "contacts": "ct",
    "leads": "ld",
    "opportunities": "opp",
    "activities": "act",
    "products": "prd",
    "opportunity_lines": "ln",
    "members": "mb",
    "invitations": "inv",
    "audit": "aud",
    "files": "fil",
}

_TABLE = {
    "companies": "companies",
    "contacts": "contacts",
    "leads": "leads",
    "opportunities": "opportunities",
    "activities": "activities",
    "products": "products",
    "opportunity_lines": "opportunity_lines",
    "members": "members",
    "invitations": "invitations",
    "audit": "audit",
    "files": "files",
}


class CrmError(Exception):
    def __init__(self, message: str, *, status: int = 400) -> None:
        super().__init__(message)
        self.message = message
        self.status = status


def _now() -> int:
    return int(time.time())


def _stable_hash(text: str) -> str:
    a = 0x811C9DC5
    for ch in text or "":
        a = ((a ^ ord(ch)) * 0x01000193) & 0xFFFFFFFF
    return f"{a:08x}"


def new_id(kind: str, seed: str = "") -> str:
    prefix = _PREFIX.get(kind, "crm")
    source = seed or uuid.uuid4().hex
    return f"{prefix}-{_stable_hash(source)}"


def _require_project(project: Path) -> Path:
    root = Path(project)
    if not root.is_dir():
        raise CrmError("project root not found", status=404)
    return root


def _trim(value: Any, max_len: int = 240) -> str:
    return str(value or "").strip()[:max_len]


def _tags(raw: Any) -> list[str]:
    if isinstance(raw, str):
        parts = [part.strip() for part in raw.split(",")]
    elif isinstance(raw, list):
        parts = [str(item).strip() for item in raw]
    else:
        parts = []
    out: list[str] = []
    seen: set[str] = set()
    for part in parts:
        key = part.lower()
        if not part or key in seen:
            continue
        seen.add(key)
        out.append(part[:40])
        if len(out) >= 12:
            break
    return out


def person_key(name: str | None) -> str:
    return " ".join(str(name or "").strip().lower().split())


def _table_for(kind: str) -> str:
    table = _TABLE.get(kind)
    if not table:
        raise CrmError(f"unknown kind: {kind}")
    return table


def _dumps(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False)


def _loads_list(raw: Any) -> list[Any]:
    if isinstance(raw, list):
        return raw
    if isinstance(raw, str) and raw.strip():
        try:
            data = json.loads(raw)
        except json.JSONDecodeError:
            return []
        return data if isinstance(data, list) else []
    return []


def _normalize(kind: str, body: dict[str, Any], *, existing: dict[str, Any] | None = None) -> dict[str, Any]:
    now = _now()
    base = dict(existing or {})
    owner = _trim(body.get("owner") or base.get("owner"), 80)
    created_by = _trim(body.get("createdBy") or base.get("createdBy") or owner, 80)
    tags = _tags(body.get("tags") if "tags" in body else base.get("tags"))
    created = int(base.get("createdAt") or now)
    record_id = str(base.get("id") or body.get("id") or new_id(kind))

    if kind == "companies":
        name = _trim(body.get("name") or base.get("name"), 120)
        if not name:
            raise CrmError("a company needs a name")
        return {
            "id": record_id,
            "name": name,
            "industry": _trim(body.get("industry") or base.get("industry"), 80),
            "country": _trim(body.get("country") or base.get("country"), 80),
            "website": _trim(body.get("website") or base.get("website"), 200),
            "phone": _trim(body.get("phone") or base.get("phone"), 40),
            "owner": owner,
            "tags": tags,
            "notes": _trim(body.get("notes") if "notes" in body else base.get("notes"), 4_000),
            "createdBy": created_by,
            "createdAt": created,
            "updatedAt": now,
        }

    if kind == "contacts":
        first = _trim(body.get("firstName") or base.get("firstName"), 80)
        last = _trim(body.get("lastName") or base.get("lastName"), 80)
        if not first and not last:
            raise CrmError("a contact needs a first or last name")
        status = _trim(body.get("status") or base.get("status") or "actif", 20).lower()
        if status not in CONTACT_STATUSES:
            status = "actif"
        return {
            "id": record_id,
            "firstName": first,
            "lastName": last,
            "title": _trim(body.get("title") or base.get("title"), 80),
            "email": _trim(body.get("email") or base.get("email"), 120),
            "phone": _trim(body.get("phone") or base.get("phone"), 40),
            "whatsapp": _trim(body.get("whatsapp") or base.get("whatsapp"), 40),
            "linkedin": _trim(body.get("linkedin") or base.get("linkedin"), 200),
            "companyId": _trim(body.get("companyId") or base.get("companyId"), 40),
            "country": _trim(body.get("country") or base.get("country"), 8).upper(),
            "owner": owner,
            "source": _trim(body.get("source") or base.get("source"), 80),
            "tags": tags,
            "status": status,
            "createdBy": created_by,
            "createdAt": created,
            "updatedAt": now,
        }

    if kind == "leads":
        name = _trim(body.get("name") or base.get("name"), 120)
        if not name:
            raise CrmError("a lead needs a name")
        status = _trim(body.get("status") or base.get("status") or "nouveau", 20).lower()
        if status not in LEAD_STAGES:
            status = "nouveau"
        try:
            score = int(body.get("score") if body.get("score") is not None else base.get("score") or 0)
        except (TypeError, ValueError):
            score = 0
        return {
            "id": record_id,
            "name": name,
            "company": _trim(body.get("company") or base.get("company"), 120),
            "email": _trim(body.get("email") or base.get("email"), 120),
            "phone": _trim(body.get("phone") or base.get("phone"), 40),
            "country": _trim(body.get("country") or base.get("country"), 8).upper(),
            "source": _trim(body.get("source") or base.get("source"), 80),
            "score": max(0, min(100, score)),
            "owner": owner,
            "status": status,
            "convertedOpportunityId": _trim(
                body.get("convertedOpportunityId") or base.get("convertedOpportunityId"),
                40,
            ),
            "createdBy": created_by,
            "createdAt": created,
            "updatedAt": now,
        }

    if kind == "opportunities":
        name = _trim(body.get("name") or base.get("name"), 160)
        if not name:
            raise CrmError("an opportunity needs a name")
        stage = _trim(body.get("stage") or base.get("stage") or "nouveau", 20).lower()
        if stage not in OPP_STAGES:
            stage = "nouveau"
        try:
            amount = float(body.get("amount") if body.get("amount") is not None else base.get("amount") or 0)
        except (TypeError, ValueError):
            amount = 0.0
        try:
            probability = int(
                body.get("probability")
                if body.get("probability") is not None
                else base.get("probability")
                or 10
            )
        except (TypeError, ValueError):
            probability = 10
        contact_ids = body.get("contactIds") if "contactIds" in body else base.get("contactIds") or []
        if not isinstance(contact_ids, list):
            contact_ids = []
        return {
            "id": record_id,
            "name": name,
            "amount": max(0.0, round(amount, 2)),
            "currency": _trim(body.get("currency") or base.get("currency") or "EUR", 8).upper() or "EUR",
            "probability": max(0, min(100, probability)),
            "stage": stage,
            "closeDate": _trim(body.get("closeDate") or base.get("closeDate"), 20),
            "companyId": _trim(body.get("companyId") or base.get("companyId"), 40),
            "contactIds": [str(item).strip() for item in contact_ids if str(item).strip()][:20],
            "owner": owner,
            "source": _trim(body.get("source") or base.get("source"), 80),
            "nextAction": _trim(body.get("nextAction") or base.get("nextAction"), 200),
            "lossReason": _trim(body.get("lossReason") or base.get("lossReason"), 200),
            "createdBy": created_by,
            "createdAt": created,
            "updatedAt": now,
        }

    if kind == "products":
        name = _trim(body.get("name") or base.get("name"), 160)
        if not name:
            raise CrmError("a product needs a name")
        try:
            price = float(
                body.get("defaultPrice")
                if body.get("defaultPrice") is not None
                else base.get("defaultPrice")
                or 0
            )
        except (TypeError, ValueError):
            price = 0.0
        return {
            "id": record_id,
            "name": name,
            "defaultPrice": max(0.0, round(price, 2)),
            "currency": _trim(body.get("currency") or base.get("currency") or "EUR", 8).upper() or "EUR",
            "owner": owner,
            "createdBy": created_by,
            "createdAt": created,
            "updatedAt": now,
        }

    if kind == "opportunity_lines":
        opp_id = _trim(body.get("opportunityId") or base.get("opportunityId"), 40)
        if not opp_id:
            raise CrmError("a line needs an opportunityId")
        try:
            qty = float(body.get("qty") if body.get("qty") is not None else base.get("qty") or 1)
        except (TypeError, ValueError):
            qty = 1.0
        try:
            unit = float(
                body.get("unitPrice")
                if body.get("unitPrice") is not None
                else base.get("unitPrice")
                or 0
            )
        except (TypeError, ValueError):
            unit = 0.0
        qty = max(0.0, qty)
        unit = max(0.0, unit)
        name = _trim(body.get("name") or base.get("name"), 160)
        return {
            "id": record_id,
            "opportunityId": opp_id,
            "productId": _trim(body.get("productId") or base.get("productId"), 40),
            "name": name,
            "qty": round(qty, 3),
            "unitPrice": round(unit, 2),
            "total": round(qty * unit, 2),
            "createdBy": created_by,
            "createdAt": created,
            "updatedAt": now,
        }

    if kind == "files":
        name = _trim(body.get("name") or base.get("name") or "file", 180)
        return {
            "id": record_id,
            "name": name,
            "mime": _trim(body.get("mime") or base.get("mime") or "application/octet-stream", 120),
            "size": int(body.get("size") if body.get("size") is not None else base.get("size") or 0),
            "path": _trim(body.get("path") or base.get("path"), 400),
            "contactId": _trim(body.get("contactId") or base.get("contactId"), 40),
            "companyId": _trim(body.get("companyId") or base.get("companyId"), 40),
            "opportunityId": _trim(body.get("opportunityId") or base.get("opportunityId"), 40),
            "leadId": _trim(body.get("leadId") or base.get("leadId"), 40),
            "owner": owner,
            "createdBy": created_by,
            "createdAt": created,
            "updatedAt": now,
        }

    kind_act = _trim(body.get("kind") or base.get("kind") or "note", 20).lower()
    if kind_act not in ACTIVITY_KINDS:
        kind_act = "note"
    title = _trim(body.get("title") or base.get("title"), 160)
    if not title:
        raise CrmError("an activity needs a title")
    try:
        at = int(body.get("at") if body.get("at") is not None else base.get("at") or now)
    except (TypeError, ValueError):
        at = now
    return {
        "id": record_id,
        "kind": kind_act,
        "title": title,
        "body": _trim(body.get("body") or base.get("body"), 4_000),
        "at": at,
        "contactId": _trim(body.get("contactId") or base.get("contactId"), 40),
        "companyId": _trim(body.get("companyId") or base.get("companyId"), 40),
        "opportunityId": _trim(body.get("opportunityId") or base.get("opportunityId"), 40),
        "leadId": _trim(body.get("leadId") or base.get("leadId"), 40),
        "owner": owner,
        "createdBy": created_by,
        "createdAt": created,
        "updatedAt": now,
    }


def _row_to_sql(kind: str, row: dict[str, Any]) -> tuple[list[str], list[Any]]:
    mapping = {
        "companies": {
            "id": row.get("id"),
            "name": row.get("name") or "",
            "industry": row.get("industry") or "",
            "country": row.get("country") or "",
            "website": row.get("website") or "",
            "phone": row.get("phone") or "",
            "owner": row.get("owner") or "",
            "tags": _dumps(row.get("tags") or []),
            "notes": row.get("notes") or "",
            "created_by": row.get("createdBy") or "",
            "created_at": int(row.get("createdAt") or _now()),
            "updated_at": int(row.get("updatedAt") or _now()),
        },
        "contacts": {
            "id": row.get("id"),
            "first_name": row.get("firstName") or "",
            "last_name": row.get("lastName") or "",
            "title": row.get("title") or "",
            "email": row.get("email") or "",
            "phone": row.get("phone") or "",
            "whatsapp": row.get("whatsapp") or "",
            "linkedin": row.get("linkedin") or "",
            "company_id": row.get("companyId") or "",
            "country": row.get("country") or "",
            "owner": row.get("owner") or "",
            "source": row.get("source") or "",
            "tags": _dumps(row.get("tags") or []),
            "status": row.get("status") or "actif",
            "created_by": row.get("createdBy") or "",
            "created_at": int(row.get("createdAt") or _now()),
            "updated_at": int(row.get("updatedAt") or _now()),
        },
        "leads": {
            "id": row.get("id"),
            "name": row.get("name") or "",
            "company": row.get("company") or "",
            "email": row.get("email") or "",
            "phone": row.get("phone") or "",
            "country": row.get("country") or "",
            "source": row.get("source") or "",
            "score": int(row.get("score") or 0),
            "owner": row.get("owner") or "",
            "status": row.get("status") or "nouveau",
            "converted_opportunity_id": row.get("convertedOpportunityId") or "",
            "created_by": row.get("createdBy") or "",
            "created_at": int(row.get("createdAt") or _now()),
            "updated_at": int(row.get("updatedAt") or _now()),
        },
        "opportunities": {
            "id": row.get("id"),
            "name": row.get("name") or "",
            "amount": float(row.get("amount") or 0),
            "currency": row.get("currency") or "EUR",
            "probability": int(row.get("probability") or 10),
            "stage": row.get("stage") or "nouveau",
            "close_date": row.get("closeDate") or "",
            "company_id": row.get("companyId") or "",
            "contact_ids": _dumps(row.get("contactIds") or []),
            "owner": row.get("owner") or "",
            "source": row.get("source") or "",
            "next_action": row.get("nextAction") or "",
            "loss_reason": row.get("lossReason") or "",
            "created_by": row.get("createdBy") or "",
            "created_at": int(row.get("createdAt") or _now()),
            "updated_at": int(row.get("updatedAt") or _now()),
        },
        "activities": {
            "id": row.get("id"),
            "kind": row.get("kind") or "note",
            "title": row.get("title") or "",
            "body": row.get("body") or "",
            "at": int(row.get("at") or _now()),
            "contact_id": row.get("contactId") or "",
            "company_id": row.get("companyId") or "",
            "opportunity_id": row.get("opportunityId") or "",
            "lead_id": row.get("leadId") or "",
            "owner": row.get("owner") or "",
            "created_by": row.get("createdBy") or "",
            "created_at": int(row.get("createdAt") or _now()),
            "updated_at": int(row.get("updatedAt") or _now()),
        },
        "products": {
            "id": row.get("id"),
            "name": row.get("name") or "",
            "default_price": float(row.get("defaultPrice") or 0),
            "currency": row.get("currency") or "EUR",
            "owner": row.get("owner") or "",
            "created_by": row.get("createdBy") or "",
            "created_at": int(row.get("createdAt") or _now()),
            "updated_at": int(row.get("updatedAt") or _now()),
        },
        "opportunity_lines": {
            "id": row.get("id"),
            "opportunity_id": row.get("opportunityId") or "",
            "product_id": row.get("productId") or "",
            "name": row.get("name") or "",
            "qty": float(row.get("qty") or 1),
            "unit_price": float(row.get("unitPrice") or 0),
            "total": float(row.get("total") or 0),
            "created_by": row.get("createdBy") or "",
            "created_at": int(row.get("createdAt") or _now()),
            "updated_at": int(row.get("updatedAt") or _now()),
        },
        "files": {
            "id": row.get("id"),
            "name": row.get("name") or "",
            "mime": row.get("mime") or "",
            "size": int(row.get("size") or 0),
            "path": row.get("path") or "",
            "contact_id": row.get("contactId") or "",
            "company_id": row.get("companyId") or "",
            "opportunity_id": row.get("opportunityId") or "",
            "lead_id": row.get("leadId") or "",
            "owner": row.get("owner") or "",
            "created_by": row.get("createdBy") or "",
            "created_at": int(row.get("createdAt") or _now()),
            "updated_at": int(row.get("updatedAt") or _now()),
        },
    }
    data = mapping.get(kind)
    if data is None:
        raise CrmError(f"unknown kind: {kind}")
    return list(data.keys()), list(data.values())


def _sql_to_row(kind: str, raw: sqlite3.Row) -> dict[str, Any]:
    item = dict(raw)
    if kind == "companies":
        return {
            "id": item["id"],
            "name": item["name"],
            "industry": item["industry"],
            "country": item["country"],
            "website": item["website"],
            "phone": item["phone"],
            "owner": item["owner"],
            "tags": _loads_list(item["tags"]),
            "notes": item.get("notes") or "",
            "createdBy": item.get("created_by") or "",
            "createdAt": item["created_at"],
            "updatedAt": item["updated_at"],
        }
    if kind == "contacts":
        return {
            "id": item["id"],
            "firstName": item["first_name"],
            "lastName": item["last_name"],
            "title": item["title"],
            "email": item["email"],
            "phone": item["phone"],
            "whatsapp": item["whatsapp"],
            "linkedin": item["linkedin"],
            "companyId": item["company_id"],
            "country": item.get("country") or "",
            "owner": item["owner"],
            "source": item["source"],
            "tags": _loads_list(item["tags"]),
            "status": item["status"],
            "createdBy": item.get("created_by") or "",
            "createdAt": item["created_at"],
            "updatedAt": item["updated_at"],
        }
    if kind == "leads":
        return {
            "id": item["id"],
            "name": item["name"],
            "company": item["company"],
            "email": item["email"],
            "phone": item["phone"],
            "country": item.get("country") or "",
            "source": item["source"],
            "score": item["score"],
            "owner": item["owner"],
            "status": item["status"],
            "convertedOpportunityId": item["converted_opportunity_id"],
            "createdBy": item.get("created_by") or "",
            "createdAt": item["created_at"],
            "updatedAt": item["updated_at"],
        }
    if kind == "opportunities":
        return {
            "id": item["id"],
            "name": item["name"],
            "amount": item["amount"],
            "currency": item["currency"],
            "probability": item["probability"],
            "stage": item["stage"],
            "closeDate": item["close_date"],
            "companyId": item["company_id"],
            "contactIds": _loads_list(item["contact_ids"]),
            "owner": item["owner"],
            "source": item["source"],
            "nextAction": item["next_action"],
            "lossReason": item["loss_reason"],
            "createdBy": item.get("created_by") or "",
            "createdAt": item["created_at"],
            "updatedAt": item["updated_at"],
        }
    if kind == "products":
        return {
            "id": item["id"],
            "name": item["name"],
            "defaultPrice": item["default_price"],
            "currency": item["currency"],
            "owner": item["owner"],
            "createdBy": item.get("created_by") or "",
            "createdAt": item["created_at"],
            "updatedAt": item["updated_at"],
        }
    if kind == "opportunity_lines":
        return {
            "id": item["id"],
            "opportunityId": item["opportunity_id"],
            "productId": item["product_id"],
            "name": item["name"],
            "qty": item["qty"],
            "unitPrice": item["unit_price"],
            "total": item["total"],
            "createdBy": item.get("created_by") or "",
            "createdAt": item["created_at"],
            "updatedAt": item["updated_at"],
        }
    if kind == "files":
        return {
            "id": item["id"],
            "name": item["name"],
            "mime": item["mime"],
            "size": item["size"],
            "path": item["path"],
            "contactId": item["contact_id"],
            "companyId": item["company_id"],
            "opportunityId": item["opportunity_id"],
            "leadId": item["lead_id"],
            "owner": item["owner"],
            "createdBy": item.get("created_by") or "",
            "createdAt": item["created_at"],
            "updatedAt": item["updated_at"],
        }
    return {
        "id": item["id"],
        "kind": item["kind"],
        "title": item["title"],
        "body": item["body"],
        "at": item["at"],
        "contactId": item["contact_id"],
        "companyId": item["company_id"],
        "opportunityId": item["opportunity_id"],
        "leadId": item["lead_id"],
        "owner": item["owner"],
        "createdBy": item.get("created_by") or "",
        "createdAt": item["created_at"],
        "updatedAt": item["updated_at"],
    }


def _conn(project: Path) -> sqlite3.Connection:
    return connect(_require_project(project))


def _append_audit(
    connection: sqlite3.Connection,
    *,
    action: str,
    kind: str = "",
    record_id: str = "",
    actor: str = "",
    detail: str = "",
) -> None:
    connection.execute(
        "INSERT INTO audit(id, action, kind, record_id, actor, detail, created_at) "
        "VALUES (?, ?, ?, ?, ?, ?, ?)",
        (new_id("audit"), action, kind, record_id, actor[:80], detail[:800], _now()),
    )


def _identity_keys(email: str = "", handle: str = "", user_id: str = "", display: str = "") -> list[str]:
    keys: list[str] = []
    for raw in (email, handle, user_id, display):
        key = person_key(raw)
        if key and key not in keys:
            keys.append(key)
    return keys


def _member_row(raw: sqlite3.Row) -> dict[str, Any]:
    return {
        "id": raw["id"],
        "identity": raw["identity"],
        "email": raw["email"],
        "handle": raw["handle"],
        "userId": raw["user_id"],
        "displayName": raw["display_name"],
        "role": raw["role"],
        "createdAt": raw["created_at"],
        "updatedAt": raw["updated_at"],
    }


def _invite_row(raw: sqlite3.Row) -> dict[str, Any]:
    return {
        "id": raw["id"],
        "identity": raw["identity"],
        "email": raw["email"],
        "handle": raw["handle"],
        "userId": raw["user_id"],
        "role": raw["role"],
        "status": raw["status"],
        "invitedBy": raw["invited_by"],
        "createdAt": raw["created_at"],
        "updatedAt": raw["updated_at"],
    }


def _find_member(connection: sqlite3.Connection, actor: str) -> dict[str, Any] | None:
    keys = _identity_keys(actor, actor, actor, actor)
    if not keys:
        return None
    rows = connection.execute("SELECT * FROM members").fetchall()
    for row in rows:
        member_keys = _identity_keys(row["email"], row["handle"], row["user_id"], row["identity"])
        if any(key in member_keys for key in keys):
            return _member_row(row)
    return None


def ensure_owner(project: Path, actor: str) -> dict[str, Any] | None:
    """First writer on a project becomes owner. Team-like local shared folder."""
    who = person_key(actor)
    if not who:
        return None
    root = _require_project(project)
    with project_lock(root):
        connection = _conn(root)
        try:
            count = int(connection.execute("SELECT COUNT(*) FROM members").fetchone()[0])
            existing = _find_member(connection, actor)
            if existing:
                return existing
            if count > 0:
                return None
            now = _now()
            email = actor.strip() if "@" in actor else ""
            handle = "" if email else actor.strip()
            row = {
                "id": new_id("members", who),
                "identity": who,
                "email": email[:120],
                "handle": handle[:80],
                "user_id": "",
                "display_name": actor.strip()[:80],
                "role": "owner",
                "created_at": now,
                "updated_at": now,
            }
            connection.execute(
                "INSERT INTO members(id, identity, email, handle, user_id, display_name, role, created_at, updated_at) "
                "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)",
                tuple(row.values()),
            )
            _append_audit(
                connection,
                action="role",
                kind="members",
                record_id=row["id"],
                actor=actor,
                detail="bootstrap owner",
            )
            connection.commit()
            return _member_row(connection.execute("SELECT * FROM members WHERE id=?", (row["id"],)).fetchone())
        finally:
            connection.close()


def role_of(project: Path, actor: str) -> str | None:
    if not person_key(actor):
        return None
    root = _require_project(project)
    with project_lock(root):
        connection = _conn(root)
        try:
            member = _find_member(connection, actor)
            return str(member["role"]) if member else None
        finally:
            connection.close()


def _require_role(project: Path, actor: str, minimum: str, *, action: str) -> None:
    if not person_key(actor):
        return
    ensure_owner(project, actor)
    role = role_of(project, actor)
    if role is None:
        raise CrmError("not a CRM member - ask an owner for an invite", status=403)
    if ROLE_RANK.get(role, -1) < ROLE_RANK[minimum]:
        raise CrmError(f"{action} needs role {minimum}+ (you are {role})", status=403)


def _owns(row: dict[str, Any], actor: str) -> bool:
    key = person_key(actor)
    if not key:
        return False
    return person_key(str(row.get("owner") or "")) == key or person_key(str(row.get("createdBy") or "")) == key


def list_records(project: Path, kind: str) -> list[dict[str, Any]]:
    root = _require_project(project)
    table = _table_for(kind)
    with project_lock(root):
        connection = _conn(root)
        try:
            rows = connection.execute(
                f"SELECT * FROM {table} ORDER BY updated_at DESC"
            ).fetchall()
            return [_sql_to_row(kind, row) for row in rows]
        finally:
            connection.close()


def get_record(project: Path, kind: str, record_id: str) -> dict[str, Any]:
    wanted = (record_id or "").strip()
    root = _require_project(project)
    table = _table_for(kind)
    with project_lock(root):
        connection = _conn(root)
        try:
            row = connection.execute(f"SELECT * FROM {table} WHERE id=?", (wanted,)).fetchone()
            if row is None:
                raise CrmError("record not found", status=404)
            return _sql_to_row(kind, row)
        finally:
            connection.close()


def _insert(connection: sqlite3.Connection, kind: str, row: dict[str, Any]) -> None:
    cols, values = _row_to_sql(kind, row)
    placeholders = ", ".join("?" for _ in cols)
    connection.execute(
        f"INSERT INTO {_table_for(kind)} ({', '.join(cols)}) VALUES ({placeholders})",
        values,
    )


def _replace(connection: sqlite3.Connection, kind: str, row: dict[str, Any]) -> None:
    cols, values = _row_to_sql(kind, row)
    assignments = ", ".join(f"{col}=?" for col in cols if col != "id")
    params = [value for col, value in zip(cols, values) if col != "id"]
    params.append(row["id"])
    connection.execute(f"UPDATE {_table_for(kind)} SET {assignments} WHERE id=?", params)


def create_record(
    project: Path,
    kind: str,
    body: dict[str, Any],
    *,
    actor: str = "",
) -> dict[str, Any]:
    if kind not in _TABLE:
        raise CrmError(f"unknown kind: {kind}")
    if actor:
        needed = "member" if kind == "activities" else "member"
        _require_role(project, actor, needed, action="create")
    root = _require_project(project)
    payload = dict(body or {})
    if actor and not payload.get("owner"):
        payload["owner"] = actor
    if actor and not payload.get("createdBy"):
        payload["createdBy"] = actor
    if kind in {"opportunities", "products"} and not _trim(payload.get("currency")):
        payload["currency"] = get_settings(root).get("currency") or "EUR"
    row = _normalize(kind, payload)
    with project_lock(root):
        connection = _conn(root)
        try:
            _insert(connection, kind, row)
            if kind == "opportunity_lines":
                _rollup_amount(connection, str(row["opportunityId"]))
            _append_audit(
                connection,
                action="create",
                kind=kind,
                record_id=row["id"],
                actor=actor or str(row.get("owner") or ""),
                detail=str(row.get("name") or row.get("title") or row["id"]),
            )
            connection.commit()
        except sqlite3.IntegrityError as exc:
            raise CrmError("record already exists") from exc
        finally:
            connection.close()
    if kind == "opportunity_lines":
        return get_record(root, kind, row["id"])
    return row


def update_record(
    project: Path,
    kind: str,
    record_id: str,
    body: dict[str, Any],
    *,
    actor: str = "",
) -> dict[str, Any]:
    root = _require_project(project)
    current = get_record(root, kind, record_id)
    if actor:
        role = role_of(root, actor)
        if role == "viewer":
            raise CrmError("viewer cannot update records", status=403)
        if role == "member" and kind != "activities" and not _owns(current, actor):
            raise CrmError("member can only update own records", status=403)
        if kind == "opportunities" and "stage" in (body or {}) and role == "member":
            raise CrmError("moving a stage needs admin+", status=403)
    updated = _normalize(kind, body if isinstance(body, dict) else {}, existing=current)
    with project_lock(root):
        connection = _conn(root)
        try:
            _replace(connection, kind, updated)
            if kind == "opportunity_lines":
                _rollup_amount(connection, str(updated["opportunityId"]))
            action = "stage" if kind == "opportunities" and current.get("stage") != updated.get("stage") else "update"
            _append_audit(
                connection,
                action=action,
                kind=kind,
                record_id=updated["id"],
                actor=actor or str(updated.get("owner") or ""),
                detail=json.dumps(
                    {key: updated.get(key) for key in body} if isinstance(body, dict) else {},
                    ensure_ascii=False,
                )[:800],
            )
            connection.commit()
        finally:
            connection.close()
    return get_record(root, kind, updated["id"]) if kind == "opportunity_lines" else updated


def delete_record(project: Path, kind: str, record_id: str, *, actor: str = "") -> dict[str, Any]:
    if actor:
        _require_role(project, actor, "admin", action="delete")
    root = _require_project(project)
    current = get_record(root, kind, record_id)
    with project_lock(root):
        connection = _conn(root)
        try:
            connection.execute(f"DELETE FROM {_table_for(kind)} WHERE id=?", (record_id.strip(),))
            if kind == "opportunity_lines":
                _rollup_amount(connection, str(current.get("opportunityId") or ""))
            _append_audit(
                connection,
                action="delete",
                kind=kind,
                record_id=record_id.strip(),
                actor=actor,
                detail=str(current.get("name") or current.get("title") or record_id),
            )
            connection.commit()
        finally:
            connection.close()
    return {"ok": True, "id": record_id.strip()}


def _rollup_amount(connection: sqlite3.Connection, opportunity_id: str) -> None:
    oid = (opportunity_id or "").strip()
    if not oid:
        return
    total = connection.execute(
        "SELECT COALESCE(SUM(total), 0) FROM opportunity_lines WHERE opportunity_id=?",
        (oid,),
    ).fetchone()[0]
    count = connection.execute(
        "SELECT COUNT(*) FROM opportunity_lines WHERE opportunity_id=?",
        (oid,),
    ).fetchone()[0]
    if int(count) <= 0:
        return
    connection.execute(
        "UPDATE opportunities SET amount=?, updated_at=? WHERE id=?",
        (round(float(total), 2), _now(), oid),
    )


def search_records(project: Path, query: str) -> dict[str, list[dict[str, Any]]]:
    needle = (query or "").strip().lower()
    out: dict[str, list[dict[str, Any]]] = {}
    if not needle:
        return {kind: [] for kind in KINDS}
    for kind in KINDS:
        hits: list[dict[str, Any]] = []
        for row in list_records(project, kind):
            blob = json.dumps(row, ensure_ascii=False).lower()
            if needle in blob:
                hits.append(row)
            if len(hits) >= 40:
                break
        out[kind] = hits
    return out


def convert_lead(
    project: Path,
    lead_id: str,
    *,
    owner: str = "",
    actor: str = "",
    fields: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Create or reuse contact + company, then opportunity. One transaction. Idempotent."""
    extra = fields if isinstance(fields, dict) else {}
    if actor:
        _require_role(project, actor, "admin", action="convert")
    root = _require_project(project)
    with project_lock(root):
        connection = _conn(root)
        try:
            result = _convert_lead_tx(connection, lead_id, owner=owner, actor=actor, fields=extra)
            connection.commit()
            return result
        except Exception:
            connection.rollback()
            raise
        finally:
            connection.close()


def _get_on_conn(connection: sqlite3.Connection, kind: str, record_id: str) -> dict[str, Any]:
    wanted = (record_id or "").strip()
    row = connection.execute(f"SELECT * FROM {_table_for(kind)} WHERE id=?", (wanted,)).fetchone()
    if row is None:
        raise CrmError("record not found", status=404)
    return _sql_to_row(kind, row)


def _try_get_on_conn(connection: sqlite3.Connection, kind: str, record_id: Any) -> dict[str, Any] | None:
    wanted = str(record_id or "").strip()
    if not wanted:
        return None
    try:
        return _get_on_conn(connection, kind, wanted)
    except CrmError:
        return None


def _create_on_conn(
    connection: sqlite3.Connection,
    kind: str,
    body: dict[str, Any],
    actor: str = "",
) -> dict[str, Any]:
    payload = dict(body or {})
    if actor and not payload.get("owner"):
        payload["owner"] = actor
    if actor and not payload.get("createdBy"):
        payload["createdBy"] = actor
    row = _normalize(kind, payload)
    _insert(connection, kind, row)
    _append_audit(
        connection,
        action="create",
        kind=kind,
        record_id=row["id"],
        actor=actor or str(row.get("owner") or ""),
        detail=str(row.get("name") or row.get("title") or row["id"]),
    )
    return row


def _find_company_by_name_on_conn(connection: sqlite3.Connection, name: str) -> dict[str, Any] | None:
    key = name.strip().lower()
    if not key:
        return None
    for row in connection.execute("SELECT * FROM companies"):
        item = _sql_to_row("companies", row)
        if str(item.get("name") or "").strip().lower() == key:
            return item
    return None


def _find_contact_by_email_on_conn(connection: sqlite3.Connection, email: str) -> dict[str, Any] | None:
    key = email.strip().lower()
    if not key:
        return None
    for row in connection.execute("SELECT * FROM contacts"):
        item = _sql_to_row("contacts", row)
        if str(item.get("email") or "").strip().lower() == key:
            return item
    return None


def _settings_on_conn(connection: sqlite3.Connection) -> dict[str, Any]:
    row = connection.execute("SELECT * FROM settings WHERE id='default'").fetchone()
    if row is None:
        return default_settings()
    return _settings_from_sql(row)


def _copy_lead_artifacts(
    connection: sqlite3.Connection,
    lead_id: str,
    *,
    contact_id: str,
    company_id: str,
    opportunity_id: str,
    actor: str,
) -> None:
    for raw in connection.execute("SELECT * FROM activities WHERE lead_id=?", (lead_id,)).fetchall():
        item = _sql_to_row("activities", raw)
        if str(item.get("title") or "") == "Lead converti":
            continue
        _create_on_conn(
            connection,
            "activities",
            {
                "kind": item.get("kind") or "note",
                "title": item.get("title") or "Note",
                "body": item.get("body") or "",
                "at": item.get("at"),
                "contactId": contact_id,
                "companyId": company_id,
                "opportunityId": opportunity_id,
                "leadId": lead_id,
                "owner": actor or item.get("owner") or "",
            },
            actor=actor,
        )
    for raw in connection.execute("SELECT * FROM files WHERE lead_id=?", (lead_id,)).fetchall():
        item = _sql_to_row("files", raw)
        _create_on_conn(
            connection,
            "files",
            {
                "name": item.get("name") or "",
                "mime": item.get("mime") or "",
                "size": item.get("size") or 0,
                "path": item.get("path") or "",
                "contactId": contact_id,
                "companyId": company_id,
                "opportunityId": opportunity_id,
                "leadId": lead_id,
                "owner": actor or item.get("owner") or "",
            },
            actor=actor,
        )


def _convert_lead_tx(
    connection: sqlite3.Connection,
    lead_id: str,
    *,
    owner: str,
    actor: str,
    fields: dict[str, Any],
) -> dict[str, Any]:
    lead = _get_on_conn(connection, "leads", lead_id)
    if lead.get("convertedOpportunityId"):
        opp = _try_get_on_conn(connection, "opportunities", lead.get("convertedOpportunityId"))
        if opp:
            return {
                "lead": lead,
                "company": _try_get_on_conn(connection, "companies", opp.get("companyId")),
                "contact": _first_contact_on_conn(connection, opp.get("contactIds") or []),
                "opportunity": opp,
                "created": False,
            }

    settings = _settings_on_conn(connection)
    who = _trim(
        fields.get("ownerId") or fields.get("owner") or owner or actor or lead.get("owner"),
        80,
    )
    company_name = _trim(
        fields.get("company") or lead.get("company") or lead.get("name") or "Company",
        120,
    ) or "Company"

    company_id = _trim(fields.get("companyId"), 40)
    if company_id:
        company = _get_on_conn(connection, "companies", company_id)
    else:
        company = _find_company_by_name_on_conn(connection, company_name)
        if company is None:
            company = _create_on_conn(
                connection,
                "companies",
                {
                    "name": company_name,
                    "country": lead.get("country"),
                    "phone": lead.get("phone"),
                    "owner": who,
                    "source": lead.get("source"),
                },
                actor=who,
            )

    contact_id = _trim(fields.get("contactId"), 40)
    if contact_id:
        contact = _get_on_conn(connection, "contacts", contact_id)
    else:
        contact = _find_contact_by_email_on_conn(connection, str(lead.get("email") or ""))
        if contact is None:
            parts = str(lead.get("name") or "").split(None, 1)
            contact = _create_on_conn(
                connection,
                "contacts",
                {
                    "firstName": parts[0] if parts else lead.get("name"),
                    "lastName": parts[1] if len(parts) > 1 else "",
                    "email": lead.get("email"),
                    "phone": lead.get("phone"),
                    "country": lead.get("country"),
                    "companyId": company["id"],
                    "owner": who,
                    "source": lead.get("source"),
                },
                actor=who,
            )

    raw_amount = fields.get("amount")
    try:
        amount = float(0 if raw_amount in (None, "") else raw_amount)
    except (TypeError, ValueError):
        amount = 0.0
    currency = _trim(fields.get("currency") or settings.get("currency") or "EUR", 8).upper() or "EUR"
    stage = _trim(fields.get("stage") or "nouveau", 20).lower()
    if stage not in OPP_STAGES:
        stage = "nouveau"
    close_date = _trim(fields.get("expectedCloseDate") or fields.get("closeDate"), 20)
    opp_name = _trim(
        fields.get("opportunityName") or fields.get("name") or f"{company_name} - Deal",
        160,
    ) or f"{company_name} - Deal"

    opportunity = _create_on_conn(
        connection,
        "opportunities",
        {
            "name": opp_name,
            "amount": max(0.0, amount),
            "currency": currency,
            "probability": 20,
            "stage": stage,
            "closeDate": close_date,
            "companyId": company["id"],
            "contactIds": [contact["id"]],
            "owner": who,
            "source": lead.get("source"),
        },
        actor=who,
    )
    _copy_lead_artifacts(
        connection,
        str(lead["id"]),
        contact_id=str(contact["id"]),
        company_id=str(company["id"]),
        opportunity_id=str(opportunity["id"]),
        actor=who,
    )
    updated = _normalize(
        "leads",
        {**lead, "status": "converti", "convertedOpportunityId": opportunity["id"]},
        existing=lead,
    )
    _replace(connection, "leads", updated)
    _create_on_conn(
        connection,
        "activities",
        {
            "kind": "note",
            "title": "Lead converti",
            "body": f"{lead.get('name')} -> contact, entreprise, opportunite",
            "contactId": contact["id"],
            "companyId": company["id"],
            "opportunityId": opportunity["id"],
            "leadId": updated["id"],
            "owner": who,
        },
        actor=who,
    )
    _append_audit(
        connection,
        action="convert",
        kind="leads",
        record_id=str(lead["id"]),
        actor=who,
        detail=opportunity["id"],
    )
    return {
        "lead": updated,
        "company": company,
        "contact": contact,
        "opportunity": opportunity,
        "created": True,
    }


def _first_contact_on_conn(connection: sqlite3.Connection, ids: list[Any]) -> dict[str, Any] | None:
    for raw in ids:
        found = _try_get_on_conn(connection, "contacts", raw)
        if found:
            return found
    return None


def _find_company_by_name(project: Path, name: str) -> dict[str, Any] | None:
    key = name.strip().lower()
    if not key:
        return None
    for row in list_records(project, "companies"):
        if str(row.get("name") or "").strip().lower() == key:
            return row
    return None


def _linked_company(project: Path, company_id: Any) -> dict[str, Any] | None:
    cid = str(company_id or "").strip()
    if not cid:
        return None
    try:
        return get_record(project, "companies", cid)
    except CrmError:
        return None


def _first_contact(project: Path, ids: list[Any]) -> dict[str, Any] | None:
    for raw in ids:
        try:
            return get_record(project, "contacts", str(raw))
        except CrmError:
            continue
    return None


def list_lines(project: Path, opportunity_id: str) -> list[dict[str, Any]]:
    root = _require_project(project)
    with project_lock(root):
        connection = _conn(root)
        try:
            rows = connection.execute(
                "SELECT * FROM opportunity_lines WHERE opportunity_id=? ORDER BY created_at",
                (opportunity_id.strip(),),
            ).fetchall()
            return [_sql_to_row("opportunity_lines", row) for row in rows]
        finally:
            connection.close()


def dashboard(project: Path) -> dict[str, Any]:
    root = _require_project(project)
    opps = list_records(root, "opportunities")
    activities = list_records(root, "activities")
    open_deals = [row for row in opps if row.get("stage") in OPEN_OPP_STAGES]
    won = [row for row in opps if row.get("stage") == WON_STAGE]
    lost = [row for row in opps if row.get("stage") == LOST_STAGE]
    month_start = _month_start()
    won_month = [row for row in won if int(row.get("updatedAt") or 0) >= month_start]
    pipeline_total = sum(float(row.get("amount") or 0) for row in open_deals)
    forecast = sum(
        float(row.get("amount") or 0) * int(row.get("probability") or 0) / 100.0
        for row in open_deals
    )
    closed = len(won) + len(lost)
    conversion = (len(won) / closed * 100.0) if closed else 0.0
    funnel = {stage: 0.0 for stage in OPP_STAGES}
    for row in opps:
        stage = str(row.get("stage") or "nouveau")
        if stage in funnel:
            funnel[stage] += float(row.get("amount") or 0)
    day_start = _day_start()
    today = [
        row
        for row in activities
        if str(row.get("kind")) in {"tache", "reunion"} and int(row.get("at") or 0) >= day_start
    ][:12]
    if not today:
        today = [row for row in activities if str(row.get("kind")) == "tache"][:8]
    top = sorted(open_deals, key=lambda row: float(row.get("amount") or 0), reverse=True)[:5]
    followups = due_followups(root, days=FOLLOWUP_DEFAULT_DAYS, create=False)
    settings = get_settings(root)
    return {
        "pipelineTotal": round(pipeline_total, 2),
        "openCount": len(open_deals),
        "wonThisMonth": len(won_month),
        "conversion": round(conversion, 1),
        "forecast": round(forecast, 2),
        "funnel": funnel,
        "todo": today,
        "topDeals": top,
        "followups": followups.get("items") or [],
        "counts": {kind: len(list_records(root, kind)) for kind in KINDS},
        "currency": settings["currency"],
        "locale": settings["locale"],
        "currencySymbol": settings["currencySymbol"],
        "settingsConfigured": settings["configured"],
        "settings": settings,
    }


def _month_start() -> int:
    now = time.gmtime()
    return int(time.mktime((now.tm_year, now.tm_mon, 1, 0, 0, 0, 0, 0, 0)))


def _day_start() -> int:
    now = time.localtime()
    return int(time.mktime((now.tm_year, now.tm_mon, now.tm_mday, 0, 0, 0, 0, 0, -1)))


def calendar_activities(project: Path, *, start: int, end: int) -> list[dict[str, Any]]:
    root = _require_project(project)
    with project_lock(root):
        connection = _conn(root)
        try:
            rows = connection.execute(
                "SELECT * FROM activities WHERE kind IN ('reunion', 'tache') "
                "AND at >= ? AND at < ? ORDER BY at",
                (int(start), int(end)),
            ).fetchall()
            return [_sql_to_row("activities", row) for row in rows]
        finally:
            connection.close()


def due_followups(
    project: Path,
    *,
    days: int = FOLLOWUP_DEFAULT_DAYS,
    create: bool = False,
    notify: bool = False,
    actor: str = "",
) -> dict[str, Any]:
    """Open deals with no activity for N days or no nextAction."""
    root = _require_project(project)
    cutoff = _now() - max(1, int(days)) * 86400
    opps = [row for row in list_records(root, "opportunities") if row.get("stage") in OPEN_OPP_STAGES]
    activities = list_records(root, "activities")
    last_by_opp: dict[str, int] = {}
    followup_ids: set[str] = set()
    for item in activities:
        oid = str(item.get("opportunityId") or "")
        if not oid:
            continue
        last_by_opp[oid] = max(last_by_opp.get(oid, 0), int(item.get("at") or 0))
        if str(item.get("title") or "").startswith("Relance automatique"):
            followup_ids.add(oid)
    items: list[dict[str, Any]] = []
    created: list[dict[str, Any]] = []
    for opp in opps:
        last = last_by_opp.get(str(opp["id"]), 0)
        stale = last == 0 or last < cutoff
        missing_next = not str(opp.get("nextAction") or "").strip()
        if not stale and not missing_next:
            continue
        reason = "sans activite" if stale else "sans prochaine action"
        item = {
            "opportunity": opp,
            "lastActivityAt": last or None,
            "reason": reason,
            "days": ((_now() - last) // 86400) if last else None,
        }
        items.append(item)
        if create and str(opp["id"]) not in followup_ids:
            task = create_record(
                root,
                "activities",
                {
                    "kind": "tache",
                    "title": f"Relance automatique - {opp.get('name')}",
                    "body": f"Deal ouvert {reason} depuis {days} jours.",
                    "opportunityId": opp["id"],
                    "companyId": opp.get("companyId"),
                    "at": _now(),
                    "owner": actor or str(opp.get("owner") or ""),
                },
                actor=actor,
            )
            created.append(task)
    return {
        "days": days,
        "items": items,
        "created": created,
        "notify": bool(notify and items),
        "message": (
            f"{len(items)} relance(s) CRM"
            if items
            else ""
        ),
    }


def list_audit(project: Path, *, kind: str = "", record_id: str = "", limit: int = 80) -> list[dict[str, Any]]:
    root = _require_project(project)
    with project_lock(root):
        connection = _conn(root)
        try:
            sql = "SELECT * FROM audit"
            params: list[Any] = []
            clauses: list[str] = []
            if kind:
                clauses.append("kind=?")
                params.append(kind)
            if record_id:
                clauses.append("record_id=?")
                params.append(record_id)
            if clauses:
                sql += " WHERE " + " AND ".join(clauses)
            sql += " ORDER BY created_at DESC LIMIT ?"
            params.append(max(1, min(200, int(limit))))
            rows = connection.execute(sql, params).fetchall()
            return [
                {
                    "id": row["id"],
                    "action": row["action"],
                    "kind": row["kind"],
                    "recordId": row["record_id"],
                    "actor": row["actor"],
                    "detail": row["detail"],
                    "createdAt": row["created_at"],
                }
                for row in rows
            ]
        finally:
            connection.close()


def list_members(project: Path) -> dict[str, Any]:
    root = _require_project(project)
    with project_lock(root):
        connection = _conn(root)
        try:
            members = [_member_row(row) for row in connection.execute("SELECT * FROM members ORDER BY created_at").fetchall()]
            invites = [
                _invite_row(row)
                for row in connection.execute(
                    "SELECT * FROM invitations ORDER BY created_at DESC"
                ).fetchall()
            ]
            return {
                "source": "local",
                "members": members,
                "invites": invites,
            }
        finally:
            connection.close()


def invite_member(
    project: Path,
    *,
    identity: str,
    role: str = "member",
    actor: str = "",
    email: str = "",
    handle: str = "",
    user_id: str = "",
) -> dict[str, Any]:
    if actor:
        _require_role(project, actor, "owner", action="invite")
    wanted = person_key(identity or email or handle or user_id)
    if not wanted:
        raise CrmError("invite needs an email, user id, or handle")
    role_name = role if role in ROLES and role != "owner" else "member"
    root = _require_project(project)
    with project_lock(root):
        connection = _conn(root)
        try:
            existing = _find_member(connection, identity or email or handle or user_id)
            if existing:
                raise CrmError("already a member")
            pending = connection.execute(
                "SELECT * FROM invitations WHERE identity=? AND status='pending'",
                (wanted,),
            ).fetchone()
            if pending:
                return {"invite": _invite_row(pending), "created": False}
            now = _now()
            mail = (email or (identity if "@" in identity else "")).strip()[:120]
            handle_v = (handle or ("" if mail else identity)).strip()[:80]
            row_id = new_id("invitations", wanted + str(now))
            connection.execute(
                "INSERT INTO invitations(id, identity, email, handle, user_id, role, status, invited_by, created_at, updated_at) "
                "VALUES (?, ?, ?, ?, ?, ?, 'pending', ?, ?, ?)",
                (row_id, wanted, mail, handle_v, user_id.strip()[:80], role_name, actor[:80], now, now),
            )
            _append_audit(
                connection,
                action="invite",
                kind="invitations",
                record_id=row_id,
                actor=actor,
                detail=wanted,
            )
            connection.commit()
            invite = _invite_row(connection.execute("SELECT * FROM invitations WHERE id=?", (row_id,)).fetchone())
            return {"invite": invite, "created": True}
        finally:
            connection.close()


def accept_invite(project: Path, *, actor: str, accept: bool = True, invite_id: str = "") -> dict[str, Any]:
    who = person_key(actor)
    if not who:
        raise CrmError("identity required to accept an invite")
    root = _require_project(project)
    with project_lock(root):
        connection = _conn(root)
        try:
            if invite_id:
                raw = connection.execute("SELECT * FROM invitations WHERE id=?", (invite_id.strip(),)).fetchone()
            else:
                raw = connection.execute(
                    "SELECT * FROM invitations WHERE status='pending' AND identity=?",
                    (who,),
                ).fetchone()
                if raw is None:
                    candidates = connection.execute(
                        "SELECT * FROM invitations WHERE status='pending'"
                    ).fetchall()
                    for row in candidates:
                        keys = _identity_keys(row["email"], row["handle"], row["user_id"], row["identity"])
                        if who in keys:
                            raw = row
                            break
            if raw is None:
                raise CrmError("no pending invite for this identity", status=404)
            invite = _invite_row(raw)
            keys = _identity_keys(invite["email"], invite["handle"], invite["userId"], invite["identity"])
            if who not in keys and person_key(actor) not in keys:
                raise CrmError("this invite is for another identity", status=403)
            now = _now()
            status = "accepted" if accept else "declined"
            if status not in INVITE_STATUSES:
                status = "declined"
            connection.execute(
                "UPDATE invitations SET status=?, updated_at=? WHERE id=?",
                (status, now, invite["id"]),
            )
            member = None
            if accept:
                member_id = new_id("members", invite["identity"])
                connection.execute(
                    "INSERT INTO members(id, identity, email, handle, user_id, display_name, role, created_at, updated_at) "
                    "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)",
                    (
                        member_id,
                        invite["identity"],
                        invite["email"],
                        invite["handle"],
                        invite["userId"],
                        actor.strip()[:80],
                        invite["role"] if invite["role"] in ROLES else "member",
                        now,
                        now,
                    ),
                )
                member = _member_row(connection.execute("SELECT * FROM members WHERE id=?", (member_id,)).fetchone())
            _append_audit(
                connection,
                action="accept" if accept else "decline",
                kind="invitations",
                record_id=invite["id"],
                actor=actor,
                detail=status,
            )
            connection.commit()
            return {"ok": True, "status": status, "member": member, "invite": {**invite, "status": status}}
        finally:
            connection.close()


def set_member_role(project: Path, member_id: str, role: str, *, actor: str = "") -> dict[str, Any]:
    if actor:
        _require_role(project, actor, "owner", action="role change")
    if role not in ROLES:
        raise CrmError("unknown role")
    root = _require_project(project)
    with project_lock(root):
        connection = _conn(root)
        try:
            raw = connection.execute("SELECT * FROM members WHERE id=?", (member_id.strip(),)).fetchone()
            if raw is None:
                raise CrmError("member not found", status=404)
            if raw["role"] == "owner" and role != "owner":
                owners = int(
                    connection.execute("SELECT COUNT(*) FROM members WHERE role='owner'").fetchone()[0]
                )
                if owners <= 1:
                    raise CrmError("cannot demote the last owner")
            connection.execute(
                "UPDATE members SET role=?, updated_at=? WHERE id=?",
                (role, _now(), member_id.strip()),
            )
            _append_audit(
                connection,
                action="role",
                kind="members",
                record_id=member_id.strip(),
                actor=actor,
                detail=role,
            )
            connection.commit()
            return _member_row(connection.execute("SELECT * FROM members WHERE id=?", (member_id.strip(),)).fetchone())
        finally:
            connection.close()


def kick_member(project: Path, member_id: str, *, actor: str = "") -> dict[str, Any]:
    if actor:
        _require_role(project, actor, "owner", action="kick")
    root = _require_project(project)
    with project_lock(root):
        connection = _conn(root)
        try:
            raw = connection.execute("SELECT * FROM members WHERE id=?", (member_id.strip(),)).fetchone()
            if raw is None:
                raise CrmError("member not found", status=404)
            if raw["role"] == "owner":
                raise CrmError("cannot kick an owner")
            connection.execute("DELETE FROM members WHERE id=?", (member_id.strip(),))
            _append_audit(
                connection,
                action="kick",
                kind="members",
                record_id=member_id.strip(),
                actor=actor,
                detail=raw["identity"],
            )
            connection.commit()
            return {"ok": True, "id": member_id.strip()}
        finally:
            connection.close()


def sync_status(project: Path) -> dict[str, Any]:
    root = _require_project(project)
    wal = wal_status(root)
    members = list_members(root)
    last_write = 0
    with project_lock(root):
        connection = _conn(root)
        try:
            row = connection.execute(
                "SELECT MAX(created_at) FROM audit"
            ).fetchone()
            last_write = int(row[0] or 0) if row else 0
            mode = connection.execute("PRAGMA journal_mode").fetchone()[0]
        finally:
            connection.close()
    return {
        "path": wal["path"],
        "wal": bool(wal["wal"]) or str(mode).lower() == "wal",
        "journalMode": mode,
        "memberCount": len(members.get("members") or []),
        "lastWrite": last_write,
        "bytes": wal["bytes"],
        "source": "local-shared-project",
        "cloud": False,
    }


def files_dir(project: Path) -> Path:
    path = crm_root(_require_project(project)) / "files"
    path.mkdir(parents=True, exist_ok=True)
    return path


_DEFAULT_CURRENCY = "EUR"
_DEFAULT_LOCALE = "fr-FR"
_CURRENCY_SYMBOLS = {
    "EUR": "€",
    "USD": "$",
    "GBP": "£",
    "CHF": "CHF",
    "CAD": "CA$",
    "AUD": "A$",
    "JPY": "¥",
    "TND": "DT",
    "MAD": "DH",
    "DZD": "DA",
}


def _currency_symbol(code: str, override: Any = None) -> str:
    custom = _trim(override, 8)
    if custom:
        return custom
    return _CURRENCY_SYMBOLS.get((code or "").upper(), (code or "EUR").upper())


def default_settings() -> dict[str, Any]:
    return {
        "companyName": "",
        "legalName": "",
        "industry": "",
        "country": "",
        "website": "",
        "phone": "",
        "email": "",
        "currency": _DEFAULT_CURRENCY,
        "currencySymbol": "€",
        "locale": _DEFAULT_LOCALE,
        "configured": False,
    }


def _settings_from_sql(raw: sqlite3.Row) -> dict[str, Any]:
    currency = _trim(raw["currency"] or _DEFAULT_CURRENCY, 8).upper() or _DEFAULT_CURRENCY
    name = _trim(raw["company_name"], 120)
    return {
        "companyName": name,
        "legalName": raw["legal_name"] or "",
        "industry": raw["industry"] or "",
        "country": raw["country"] or "",
        "website": raw["website"] or "",
        "phone": raw["phone"] or "",
        "email": raw["email"] or "",
        "currency": currency,
        "currencySymbol": _currency_symbol(currency, raw["currency_symbol"]),
        "locale": _trim(raw["locale"] or _DEFAULT_LOCALE, 16) or _DEFAULT_LOCALE,
        "configured": bool(int(raw["configured"] or 0) and name),
        "updatedAt": int(raw["updated_at"] or 0),
    }


def get_settings(project: Path) -> dict[str, Any]:
    root = _require_project(project)
    with project_lock(root):
        connection = _conn(root)
        try:
            row = connection.execute("SELECT * FROM settings WHERE id='default'").fetchone()
            if row is not None:
                return _settings_from_sql(row)
        finally:
            connection.close()
    path = crm_root(root) / "settings.json"
    if path.is_file():
        try:
            raw = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, UnicodeDecodeError, json.JSONDecodeError):
            raw = None
        if isinstance(raw, dict) and _trim(raw.get("companyName")):
            currency = _trim(raw.get("currency") or _DEFAULT_CURRENCY, 8).upper() or _DEFAULT_CURRENCY
            return {
                **default_settings(),
                "companyName": _trim(raw.get("companyName"), 120),
                "legalName": _trim(raw.get("legalName"), 160),
                "industry": _trim(raw.get("industry"), 80),
                "country": _trim(raw.get("country"), 80),
                "website": _trim(raw.get("website"), 200),
                "phone": _trim(raw.get("phone"), 40),
                "email": _trim(raw.get("email"), 120),
                "currency": currency,
                "currencySymbol": _currency_symbol(currency, raw.get("currencySymbol")),
                "locale": _trim(raw.get("locale") or _DEFAULT_LOCALE, 16) or _DEFAULT_LOCALE,
                "configured": True,
            }
    return default_settings()


def update_settings(project: Path, body: dict[str, Any], *, actor: str = "") -> dict[str, Any]:
    root = _require_project(project)
    current = get_settings(root)
    if actor:
        if current.get("configured"):
            _require_role(root, actor, "admin", action="settings")
        else:
            ensure_owner(root, actor)
    merged = {**current, **(body if isinstance(body, dict) else {})}
    name = _trim(merged.get("companyName"), 120)
    if not name:
        raise CrmError("a company name is required")
    currency = _trim(merged.get("currency") or _DEFAULT_CURRENCY, 8).upper() or _DEFAULT_CURRENCY
    if len(currency) != 3 or not currency.isalpha():
        raise CrmError("currency must be a 3-letter ISO code")
    locale = _trim(merged.get("locale") or _DEFAULT_LOCALE, 16) or _DEFAULT_LOCALE
    if isinstance(body, dict) and "currencySymbol" in body and _trim(body.get("currencySymbol")):
        symbol = _trim(body.get("currencySymbol"), 8)
    else:
        symbol = _currency_symbol(currency)
    now = _now()
    row = {
        "companyName": name,
        "legalName": _trim(merged.get("legalName"), 160),
        "industry": _trim(merged.get("industry"), 80),
        "country": _trim(merged.get("country"), 80),
        "website": _trim(merged.get("website"), 200),
        "phone": _trim(merged.get("phone"), 40),
        "email": _trim(merged.get("email"), 120),
        "currency": currency,
        "currencySymbol": symbol,
        "locale": locale,
        "configured": True,
        "updatedAt": now,
    }
    with project_lock(root):
        connection = _conn(root)
        try:
            connection.execute(
                "INSERT OR REPLACE INTO settings("
                "id, company_name, legal_name, industry, country, website, phone, email, "
                "currency, currency_symbol, locale, configured, updated_at) "
                "VALUES ('default', ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 1, ?)",
                (
                    row["companyName"],
                    row["legalName"],
                    row["industry"],
                    row["country"],
                    row["website"],
                    row["phone"],
                    row["email"],
                    row["currency"],
                    row["currencySymbol"],
                    row["locale"],
                    now,
                ),
            )
            _append_audit(
                connection,
                action="settings",
                kind="settings",
                record_id="default",
                actor=actor,
                detail=f"{name} {currency}",
            )
            connection.commit()
        finally:
            connection.close()
    return row
