"""JSON store for the Leads desk under the instance data dir."""

from __future__ import annotations

import json
import os
import tempfile
import time
import uuid
from pathlib import Path
from typing import Any

from navin.config.paths import get_runtime_subdir
from navin.leads.errors import LeadsError
from navin.leads.sources import STAGES

SCHEMA = 1
SECRET_KEYS = (
    "apollo",
    "hunter",
    "pdl",
    "pappers",
    "companies_house",
    "places",
    "crunchbase",
    "opencorporates",
)


def _now() -> float:
    return time.time()


def _atomic_write(path: Path, payload: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    text = json.dumps(payload, indent=2, ensure_ascii=False, sort_keys=True)
    fd, tmp = tempfile.mkstemp(prefix=".navin-leads-", suffix=".tmp", dir=str(path.parent))
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as handle:
            handle.write(text)
            handle.write("\n")
        os.replace(tmp, path)
    except Exception:
        try:
            os.unlink(tmp)
        except OSError:
            pass
        raise


def _read_json(path: Path, fallback: Any) -> Any:
    if not path.is_file():
        return fallback
    try:
        raw = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return fallback
    return raw if raw is not None else fallback


def _split(value: Any) -> list[str]:
    if isinstance(value, list):
        return [str(item).strip() for item in value if str(item).strip()]
    if isinstance(value, str):
        return [part.strip() for part in value.replace(";", ",").split(",") if part.strip()]
    return []


def default_channels() -> dict[str, Any]:
    return {
        "email": False,
        "teams": False,
        "whatsapp": False,
        "telegram": False,
        "email_to": "",
        "teams_to": "",
        "whatsapp_to": "",
        "telegram_to": "",
    }


def merge_channels(raw: Any) -> dict[str, Any]:
    base = default_channels()
    incoming = raw if isinstance(raw, dict) else {}
    for key in base:
        if key.endswith("_to"):
            base[key] = str(incoming.get(key) or "").strip()[:160]
        elif key in incoming:
            base[key] = bool(incoming.get(key))
    return base


def default_profile() -> dict[str, Any]:
    return {
        "schema": SCHEMA,
        "icp_name": "",
        "countries": ["FR"],
        "sector": "",
        "size_min": 20,
        "size_max": 200,
        "titles": ["CEO", "CTO", "Head of Data"],
        "count": 50,
        # What you sell, in one line: the outreach drafts and the qualification read it.
        "offer": "",
        # Cities to focus on (empty = the main cities of each country).
        "cities": [],
        # Extra search keywords (product names, niches, certifications).
        "keywords": [],
        # Roles a prospect hires when it needs the offer: the hiring buying signal.
        "signals": [],
        # Sources the hunt may use. Registries and paid BYOK are always on when keyed.
        "sources": ["web", "osm", "hiring"],
        # "approval": the loop drafts and waits. "autonomous": the loop sends due steps itself.
        "execution_mode": "approval",
        "daily_send_cap": 20,
        "sender_name": "",
        "wizard_ready": False,
        "updated_at": 0,
        "last_watch": 0,
        "channels": default_channels(),
    }


def normalize_profile(raw: Any) -> dict[str, Any]:
    base = default_profile()
    incoming = raw if isinstance(raw, dict) else {}
    base["icp_name"] = str(incoming.get("icp_name") or incoming.get("name") or "").strip()[:120]
    countries = [part.upper()[:2] for part in _split(incoming.get("countries"))]
    base["countries"] = countries or ["FR"]
    base["sector"] = str(incoming.get("sector") or "").strip()[:80]
    try:
        base["size_min"] = max(1, int(incoming.get("size_min") or 20))
    except (TypeError, ValueError):
        base["size_min"] = 20
    try:
        base["size_max"] = max(base["size_min"], int(incoming.get("size_max") or 200))
    except (TypeError, ValueError):
        base["size_max"] = max(base["size_min"], 200)
    titles = _split(incoming.get("titles"))
    base["titles"] = titles or ["CEO", "CTO"]
    try:
        base["count"] = max(5, min(1000, int(incoming.get("count") or 50)))
    except (TypeError, ValueError):
        base["count"] = 50
    base["offer"] = str(incoming.get("offer") or "").strip()[:400]
    base["cities"] = [part[:60] for part in _split(incoming.get("cities"))][:12]
    base["keywords"] = [part[:60] for part in _split(incoming.get("keywords"))][:8]
    base["signals"] = [part[:60] for part in _split(incoming.get("signals"))][:5]
    if "sources" in incoming:
        picked = [part.lower() for part in _split(incoming.get("sources"))]
        base["sources"] = [item for item in ("web", "osm", "hiring") if item in picked]
    mode = str(incoming.get("execution_mode") or "approval").strip().lower()
    base["execution_mode"] = mode if mode in {"approval", "autonomous"} else "approval"
    try:
        base["daily_send_cap"] = max(0, min(200, int(incoming.get("daily_send_cap", 20))))
    except (TypeError, ValueError):
        base["daily_send_cap"] = 20
    base["sender_name"] = str(incoming.get("sender_name") or "").strip()[:80]
    base["wizard_ready"] = bool(
        incoming.get("wizard_ready")
        or incoming.get("wizard_complete")
        or (base["icp_name"] and base["sector"])
    )
    try:
        base["updated_at"] = float(incoming.get("updated_at") or 0)
    except (TypeError, ValueError):
        base["updated_at"] = 0
    try:
        base["last_watch"] = float(incoming.get("last_watch") or 0)
    except (TypeError, ValueError):
        base["last_watch"] = 0
    previous = incoming.get("channels")
    base["channels"] = merge_channels(previous)
    return base


def lead_key(row: dict[str, Any]) -> str:
    email = str(row.get("email") or "").strip().casefold()
    if email:
        return f"e:{email}"
    domain = str(row.get("domain") or "").strip().casefold()
    person = str(row.get("person") or "").strip().casefold()
    if domain and person:
        return f"p:{domain}:{person}"
    company = str(row.get("company") or "").strip().casefold()
    return f"c:{company}:{domain}"


# Qualification and cadence must be replaceable even when the new value is empty.
_REPLACE_ALWAYS = frozenset(
    {"why", "signals", "bant", "disqualify", "next_action", "tier", "score", "sequence", "alerts_sent", "gaps"}
)


def merge_lead(existing: dict[str, Any], incoming: dict[str, Any]) -> dict[str, Any]:
    out = dict(existing)
    for key, value in incoming.items():
        if key in {"id", "created_at"}:
            continue
        if key in _REPLACE_ALWAYS:
            if value is None:
                continue
            out[key] = value
            continue
        if value in (None, "", [], {}):
            continue
        if key == "email" and out.get("email") and out.get("email_status") == "verified":
            continue
        if key == "email_status" and out.get("email_status") == "verified":
            continue
        out[key] = value
    out["updated_at"] = _now()
    return out


class LeadsStore:
    """Instance-level leads book: profile, rows, BYOK secrets."""

    def __init__(self, root: Path | None = None) -> None:
        self.root = root or get_runtime_subdir("leads")
        self.root.mkdir(parents=True, exist_ok=True)
        self.profile_path = self.root / "profile.json"
        self.leads_path = self.root / "leads.json"
        self.secrets_path = self.root / "secrets.json"
        self.journal_path = self.root / "journal.jsonl"
        self.loop_path = self.root / "loop.json"
        self.loop_intent_path = self.root / "loop.intent.json"
        self.cursor_path = self.root / "cursor.json"
        self.optout_path = self.root / "optout.json"
        self.sends_path = self.root / "sends.json"

    def load_profile(self) -> dict[str, Any]:
        return normalize_profile(_read_json(self.profile_path, {}))

    # -- hunt rotation ---------------------------------------------------------

    def load_cursor(self) -> int:
        raw = _read_json(self.cursor_path, {})
        try:
            return max(0, int((raw or {}).get("hunt") or 0)) if isinstance(raw, dict) else 0
        except (TypeError, ValueError):
            return 0

    def bump_cursor(self) -> int:
        """Each hunt explores the next cities / angles so a daily loop keeps finding new rows."""
        value = self.load_cursor() + 1
        _atomic_write(self.cursor_path, {"hunt": value, "updated_at": _now()})
        return value

    # -- opt-out (GDPR / CAN-SPAM) -----------------------------------------------

    def load_optout(self) -> list[str]:
        raw = _read_json(self.optout_path, [])
        if not isinstance(raw, list):
            return []
        return sorted({str(item).strip().casefold() for item in raw if str(item).strip()})

    def add_optout(self, value: str) -> list[str]:
        """An email or a whole domain. Sequences stop and the row is never contacted again."""
        key = str(value or "").strip().casefold()
        if not key:
            raise LeadsError("email or domain is required", status=400)
        rows = set(self.load_optout())
        rows.add(key)
        _atomic_write(self.optout_path, sorted(rows))
        return sorted(rows)

    def is_opted_out(self, row: dict[str, Any]) -> bool:
        blocked = set(self.load_optout())
        if not blocked:
            return False
        email = str(row.get("email") or "").strip().casefold()
        domain = str(row.get("domain") or "").strip().casefold() or (
            email.split("@")[-1] if "@" in email else ""
        )
        return bool((email and email in blocked) or (domain and domain in blocked))

    # -- daily send counter -----------------------------------------------------------

    def sends_today(self, *, now: float | None = None) -> int:
        raw = _read_json(self.sends_path, {})
        stamp = now if now is not None else _now()
        day = time.strftime("%Y-%m-%d", time.gmtime(stamp))
        if not isinstance(raw, dict) or raw.get("day") != day:
            return 0
        try:
            return int(raw.get("count") or 0)
        except (TypeError, ValueError):
            return 0

    def record_send(self, *, now: float | None = None) -> int:
        stamp = now if now is not None else _now()
        day = time.strftime("%Y-%m-%d", time.gmtime(stamp))
        count = self.sends_today(now=stamp) + 1
        _atomic_write(self.sends_path, {"day": day, "count": count, "updated_at": stamp})
        return count

    def save_profile(self, profile: dict[str, Any]) -> dict[str, Any]:
        current = self.load_profile()
        incoming = profile if isinstance(profile, dict) else {}
        merged = {**current, **incoming}
        if "channels" in incoming:
            merged["channels"] = merge_channels(
                {**(current.get("channels") or {}), **(incoming.get("channels") or {})}
            )
        next_profile = normalize_profile(merged)
        next_profile["updated_at"] = _now()
        _atomic_write(self.profile_path, next_profile)
        return next_profile

    def load_leads(self) -> list[dict[str, Any]]:
        raw = _read_json(self.leads_path, [])
        if not isinstance(raw, list):
            return []
        return [row for row in raw if isinstance(row, dict) and row.get("id")]

    def save_leads(self, rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
        _atomic_write(self.leads_path, rows)
        return rows

    def upsert_leads(self, incoming: list[dict[str, Any]]) -> tuple[list[dict[str, Any]], int]:
        current = self.load_leads()
        by_key = {lead_key(row): row for row in current}
        added = 0
        for row in incoming:
            if not isinstance(row, dict):
                continue
            company = str(row.get("company") or "").strip()
            if not company:
                continue
            payload = dict(row)
            payload.setdefault("id", f"ld-{uuid.uuid4().hex[:12]}")
            payload.setdefault("stage", "new")
            if payload.get("stage") not in STAGES:
                payload["stage"] = "new"
            payload.setdefault("created_at", _now())
            payload["updated_at"] = _now()
            key = lead_key(payload)
            if key in by_key:
                by_key[key] = merge_lead(by_key[key], payload)
            else:
                by_key[key] = payload
                added += 1
        rows = list(by_key.values())
        self.save_leads(rows)
        return rows, added

    def get_lead(self, lead_id: str) -> dict[str, Any]:
        wanted = (lead_id or "").strip()
        for row in self.load_leads():
            if str(row.get("id") or "") == wanted:
                return row
        raise LeadsError("lead not found", status=404)

    def patch_lead(self, lead_id: str, patch: dict[str, Any]) -> dict[str, Any]:
        rows = self.load_leads()
        found = False
        for index, row in enumerate(rows):
            if str(row.get("id") or "") != lead_id:
                continue
            next_row = merge_lead(row, patch)
            if "stage" in patch:
                stage = str(patch.get("stage") or "").strip()
                if stage not in STAGES:
                    raise LeadsError("unknown stage")
                next_row["stage"] = stage
            rows[index] = next_row
            found = True
            break
        if not found:
            raise LeadsError("lead not found", status=404)
        self.save_leads(rows)
        return next(row for row in rows if str(row.get("id") or "") == lead_id)

    def delete_lead(self, lead_id: str) -> dict[str, Any]:
        wanted = (lead_id or "").strip()
        if not wanted:
            raise LeadsError("id is required")
        rows = self.load_leads()
        kept = [row for row in rows if str(row.get("id") or "") != wanted]
        if len(kept) == len(rows):
            raise LeadsError("lead not found", status=404)
        self.save_leads(kept)
        return {"id": wanted}

    def load_secrets(self) -> dict[str, str]:
        raw = _read_json(self.secrets_path, {})
        if not isinstance(raw, dict):
            return {}
        out: dict[str, str] = {}
        for key in SECRET_KEYS:
            value = str(raw.get(key) or "").strip()
            if value:
                out[key] = value
        return out

    def save_secrets(self, incoming: dict[str, Any]) -> dict[str, str]:
        current = self.load_secrets()
        if not isinstance(incoming, dict):
            raise LeadsError("secrets payload must be an object")
        for key in SECRET_KEYS:
            if key not in incoming:
                continue
            value = str(incoming.get(key) or "").strip()
            if value in {"", "-", "clear"}:
                current.pop(key, None)
            else:
                current[key] = value
        _atomic_write(self.secrets_path, current)
        return current

    def provider_status(self) -> list[dict[str, Any]]:
        from navin.leads.sources import catalog

        secrets = self.load_secrets()
        out: list[dict[str, Any]] = []
        for row in catalog():
            item = dict(row)
            key = str(item.get("id") or "")
            secret = secrets.get(key, "")
            item["configured"] = bool(secret) or item.get("kind") in {"free", "reference"}
            item["hint"] = f"…{secret[-4:]}" if len(secret) >= 4 else ("" if not secret else "set")
            item["needs_key"] = item.get("kind") == "byok" and not secret
            item["wired"] = True
            out.append(item)
        return out

    def load_loop(self) -> dict[str, Any]:
        raw = _read_json(self.loop_path, {})
        if not isinstance(raw, dict) or not raw:
            raw = {
                "enabled": False,
                "phase": "idle",
                "next_due": 0.0,
                "last_tick": 0.0,
                "last_result": "loop is paused - start it from the Leads desk",
                "cycle": 0,
                "skipped_reason": "",
                "schedule": {
                    "kind": "daily",
                    "hour": 9,
                    "minute": 0,
                    "weekday": 1,
                    "day": 1,
                    "tz": None,
                    "expr": "0 9 * * *",
                },
            }
            self.save_loop(raw)
        return raw

    def save_loop(self, data: dict[str, Any]) -> dict[str, Any]:
        data = dict(data)
        data["updated_at"] = _now()
        _atomic_write(self.loop_path, data)
        return data

    def load_loop_intent(self) -> dict[str, Any]:
        raw = _read_json(self.loop_intent_path, {})
        return raw if isinstance(raw, dict) else {}

    def save_loop_intent(self, data: dict[str, Any]) -> dict[str, Any]:
        payload = {key: value for key, value in dict(data).items() if key != "updated_at"}
        payload["updated_at"] = _now()
        _atomic_write(self.loop_intent_path, payload)
        return payload

    def clear_loop_intent(self) -> None:
        try:
            self.loop_intent_path.unlink()
        except FileNotFoundError:
            return

    def append_journal(self, event: dict[str, Any]) -> None:
        line = json.dumps({**event, "ts": _now()}, ensure_ascii=False)
        self.journal_path.parent.mkdir(parents=True, exist_ok=True)
        with self.journal_path.open("a", encoding="utf-8") as handle:
            handle.write(line + "\n")
