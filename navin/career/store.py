# Copyright (c) 2026-present Navinspire IA
# SPDX-License-Identifier: AGPL-3.0-only

"""JSON store for the Career desk under the instance data dir."""

from __future__ import annotations

import base64
import json
import os
import re
import tempfile
import time
import uuid
from pathlib import Path
from typing import Any

from navin.career.errors import CareerError
from navin.career.mail_settings import default_mailbox, normalize_mailbox
from navin.career.sources import APPLY_MODES, STAGES, TRACKS, html_to_text, normalize_job_url
from navin.config.paths import get_runtime_subdir

SCHEMA = 1

CAREER_SECRET_NAMES = frozenset(
    {
        "ADZUNA_APP_ID",
        "ADZUNA_APP_KEY",
        "JOOBLE_API_KEY",
        "USAJOBS_API_KEY",
        "USAJOBS_USER_AGENT",
        "CAREER_SMTP_PASSWORD",
        "CAREER_IMAP_PASSWORD",
    }
)


def _now() -> float:
    return time.time()


def _atomic_write_text(path: Path, text: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp = tempfile.mkstemp(prefix=".navin-career-", suffix=".tmp", dir=str(path.parent))
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as handle:
            handle.write(text)
            if text and not text.endswith("\n"):
                handle.write("\n")
        os.replace(tmp, path)
    except Exception:
        try:
            os.unlink(tmp)
        except OSError:
            pass
        raise


def _atomic_write(path: Path, payload: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    text = json.dumps(payload, indent=2, ensure_ascii=False, sort_keys=True)
    fd, tmp = tempfile.mkstemp(prefix=".navin-career-", suffix=".tmp", dir=str(path.parent))
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


def _dedupe_applications(rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    by_oid: dict[str, dict[str, Any]] = {}
    orphans: list[dict[str, Any]] = []
    for row in rows:
        if not isinstance(row, dict):
            continue
        oid = str(row.get("opportunity_id") or "").strip()
        if not oid:
            orphans.append(row)
            continue
        prev = by_oid.get(oid)
        if prev is None or float(row.get("updated_at") or 0) >= float(prev.get("updated_at") or 0):
            by_oid[oid] = row
    return list(by_oid.values()) + orphans


def _empty_company() -> dict[str, Any]:
    return {
        "name": "",
        "email": "",
        "phone": "",
        "address": "",
        "city": "",
        "country": "",
    }


def _normalize_experiences(raw: Any) -> list[dict[str, str]]:
    rows: list[dict[str, str]] = []
    if not isinstance(raw, list):
        return rows
    for item in raw:
        if not isinstance(item, dict):
            continue
        title = str(item.get("title") or "").strip()
        company = str(item.get("company") or "").strip()
        period = str(item.get("period") or "").strip()
        facts = str(item.get("facts") or item.get("detail") or "").strip()
        if title or company or period or facts:
            rows.append({"title": title, "company": company, "period": period, "facts": facts})
    return rows


def _normalize_education(raw: Any) -> list[dict[str, str]]:
    rows: list[dict[str, str]] = []
    if not isinstance(raw, list):
        return rows
    for item in raw:
        if not isinstance(item, dict):
            continue
        school = str(item.get("school") or "").strip()
        diploma = str(item.get("diploma") or item.get("title") or "").strip()
        year = str(item.get("year") or "").strip()
        if school or diploma or year:
            rows.append({"school": school, "diploma": diploma, "year": year})
    return rows


def _empty_channels() -> dict[str, Any]:
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


def default_profile() -> dict[str, Any]:
    return {
        "schema": SCHEMA,
        "track": "freelance",
        "account_kind": "solo",
        "wizard_complete": False,
        "wizard_step": 1,
        "display_name": "",
        "headline": "",
        "email": "",
        "phone": "",
        "residence_country": "",
        "titles": [],
        "engagement": "freelance",
        "countries_primary": [],
        "countries_secondary": [],
        "countries_excluded": [],
        "country_weights": {},
        "work_mode": "remote",
        "hybrid_days_min": 0,
        "hybrid_days_max": 0,
        "min_rate": 0,
        "max_rate": 0,
        "min_salary": 0,
        "max_salary": 0,
        "currency": "EUR",
        "available_from": "",
        "languages": ["fr", "en"],
        "visa": "none",
        "stack": [],
        "industries_priority": [],
        "industries_excluded": [],
        "master_cv": "",
        "cv_path": "",
        "experiences": [],
        "education": [],
        "strengths": [],
        "weaknesses": [],
        "highlights": [],
        "projects": [],
        "prospect_email": "",
        "prospect_email_approved": False,
        "apply_mode": "manual",
        "ai_assist": False,
        "mail": {"gmail": False, "outlook": False},
        "mailbox": default_mailbox(),
        "channels": _empty_channels(),
        "company": _empty_company(),
        "talents": [],
        "active_talent_id": "",
        "source_ids": [],
        "ats_boards": [],
        # ESN / consulting / agency feeds per market. `employers` are the houses
        # the user added (name + careers URL); `employers_hidden` mutes directory rows.
        "employer_watch": True,
        "employers": [],
        "employers_hidden": [],
        "archive_after_days": 45,
        "delete_after_days": 60,
    }


def normalize_profile(raw: dict[str, Any] | None) -> dict[str, Any]:
    base = default_profile()
    incoming = dict(raw) if isinstance(raw, dict) else {}
    incoming.pop("api_keys", None)
    for secret_field in ("smtp_password", "imap_password", "mailbox_status"):
        incoming.pop(secret_field, None)
    base.update(incoming)
    base.pop("api_keys", None)
    track = str(base.get("track") or "freelance").strip().lower()
    if track not in TRACKS and track != "both":
        track = "freelance"
    base["track"] = track
    mode = str(base.get("apply_mode") or "manual").strip().lower()
    if mode not in APPLY_MODES:
        mode = "manual"
    base["apply_mode"] = mode
    base["ai_assist"] = incoming.get("ai_assist", base.get("ai_assist")) is True
    kind = str(base.get("account_kind") or "solo").strip().lower()
    base["account_kind"] = "company" if kind == "company" else "solo"
    base["wizard_complete"] = bool(base.get("wizard_complete"))
    try:
        step = int(base.get("wizard_step") or 1)
    except (TypeError, ValueError):
        step = 1
    base["wizard_step"] = min(10, max(1, step))
    path = str(base.get("cv_path") or "").strip().lower()
    base["cv_path"] = path if path in {"import", "create"} else ""
    base["display_name"] = str(base.get("display_name") or "")
    base["headline"] = str(base.get("headline") or "")
    base["email"] = str(base.get("email") or "")
    base["phone"] = str(base.get("phone") or "")
    residence = str(base.get("residence_country") or "").strip().upper()
    base["residence_country"] = residence[:2] if len(residence) >= 2 else residence
    for key in (
        "titles",
        "countries_primary",
        "countries_secondary",
        "countries_excluded",
        "languages",
        "stack",
        "industries_priority",
        "industries_excluded",
        "ats_boards",
        "strengths",
        "weaknesses",
        "highlights",
        "source_ids",
    ):
        if key in incoming or not isinstance(base.get(key), list):
            values = _split(incoming.get(key, base.get(key)))
            if key.startswith("countries") or key == "languages":
                values = [item.upper() if key.startswith("countries") else item.lower()[:8] for item in values]
            if key.startswith("countries"):
                values = [item.upper() for item in values]
            if key == "ats_boards":
                values = [item.lower()[:40] for item in values]
            if key == "source_ids":
                values = [item.lower()[:40] for item in values]
            base[key] = values
    base["employer_watch"] = incoming.get("employer_watch", base.get("employer_watch", True)) is not False
    base["employers_hidden"] = [item.lower()[:80] for item in _split(base.get("employers_hidden"))]
    employers: list[dict[str, Any]] = []
    for raw_employer in base.get("employers") or []:
        if not isinstance(raw_employer, dict):
            continue
        name = str(raw_employer.get("name") or "").strip()[:120]
        url = str(raw_employer.get("url") or raw_employer.get("careers_url") or "").strip()[:400]
        if url and not url.lower().startswith(("http://", "https://")):
            url = "https://" + url
        if not name and not url:
            continue
        markets = [str(item).strip().upper()[:2] for item in _split(raw_employer.get("markets") or raw_employer.get("country")) if str(item).strip()]
        employers.append(
            {
                "name": name or url,
                "url": url,
                "kind": str(raw_employer.get("kind") or "esn").strip().lower()[:20],
                "markets": markets,
            }
        )
    base["employers"] = employers[:200]
    from navin.career.sources import default_country_weights

    weights = base.get("country_weights") if isinstance(base.get("country_weights"), dict) else {}
    base["country_weights"] = default_country_weights(
        list(base.get("countries_primary") or []),
        list(base.get("countries_secondary") or []),
        list(base.get("countries_excluded") or []),
        weights,
    )
    for money_key in ("min_rate", "max_rate", "min_salary", "max_salary"):
        try:
            base[money_key] = float(base.get(money_key) or 0)
        except (TypeError, ValueError):
            base[money_key] = 0
    for day_key in ("hybrid_days_min", "hybrid_days_max"):
        try:
            base[day_key] = max(0, min(7, int(base.get(day_key) or 0)))
        except (TypeError, ValueError):
            base[day_key] = 0
    mail = base.get("mail") if isinstance(base.get("mail"), dict) else {}
    base["mail"] = {"gmail": bool(mail.get("gmail")), "outlook": bool(mail.get("outlook"))}
    base["mailbox"] = normalize_mailbox(base.get("mailbox"))
    channels = base.get("channels") if isinstance(base.get("channels"), dict) else {}
    empty_channels = _empty_channels()
    empty_channels.update({key: channels.get(key, empty_channels[key]) for key in empty_channels})
    for flag in ("email", "teams", "whatsapp", "telegram"):
        empty_channels[flag] = bool(empty_channels.get(flag))
    base["channels"] = empty_channels
    company = base.get("company") if isinstance(base.get("company"), dict) else {}
    empty_company = _empty_company()
    empty_company.update({key: str(company.get(key) or "") for key in empty_company})
    if empty_company["country"]:
        empty_company["country"] = empty_company["country"].upper()[:2]
    base["company"] = empty_company
    projects: list[dict[str, str]] = []
    raw_projects = base.get("projects")
    if isinstance(raw_projects, list):
        for item in raw_projects:
            if isinstance(item, dict):
                title = str(item.get("title") or "").strip()
                result = str(item.get("result") or item.get("detail") or "").strip()
                if title or result:
                    projects.append({"title": title, "result": result})
            elif str(item).strip():
                projects.append({"title": str(item).strip(), "result": ""})
    base["projects"] = projects
    base["experiences"] = _normalize_experiences(base.get("experiences"))
    base["education"] = _normalize_education(base.get("education"))
    talents: list[dict[str, Any]] = []
    raw_talents = base.get("talents")
    if isinstance(raw_talents, list):
        for item in raw_talents:
            if not isinstance(item, dict):
                continue
            talents.append(
                {
                    "id": str(item.get("id") or uuid.uuid4().hex[:8]),
                    "name": str(item.get("name") or ""),
                    "headline": str(item.get("headline") or ""),
                    "titles": _split(item.get("titles")),
                    "master_cv": str(item.get("master_cv") or ""),
                    "experiences": _normalize_experiences(item.get("experiences")),
                    "education": _normalize_education(item.get("education")),
                    "strengths": _split(item.get("strengths")),
                    "weaknesses": _split(item.get("weaknesses")),
                    "stack": _split(item.get("stack")),
                }
            )
    base["talents"] = talents
    base["active_talent_id"] = str(base.get("active_talent_id") or (talents[0]["id"] if talents else ""))
    base["master_cv"] = str(base.get("master_cv") or "")
    base["prospect_email"] = str(base.get("prospect_email") or "")
    base["prospect_email_approved"] = bool(base.get("prospect_email_approved"))
    from navin.career.retention import retention_days

    archive_after, delete_after = retention_days(base)
    base["archive_after_days"] = archive_after
    base["delete_after_days"] = delete_after
    return base


class CareerStore:
    """On-disk career desk. Shared by Freelance and Jobs."""

    def __init__(self, root: Path | str | None = None) -> None:
        self.root = Path(root) if root is not None else get_runtime_subdir("career")
        self.root.mkdir(parents=True, exist_ok=True)
        self.profile_path = self.root / "profile.json"
        self.jobs_path = self.root / "opportunities.json"
        self.apps_path = self.root / "applications.json"
        self.inbox_path = self.root / "inbox.json"
        self.journal_path = self.root / "journal.json"
        self.loop_path = self.root / "loop.json"
        self.loop_intent_path = self.root / "loop.intent.json"
        self.secrets_path = self.root / "secrets.json"
        self.employers_path = self.root / "employers.json"
        self.files_dir = self.root / "files"

    def load_employer_state(self) -> dict[str, Any]:
        """Per-employer feed resolution (ATS, slug, careers URL) and last check."""
        raw = _read_json(self.employers_path, {})
        return dict(raw) if isinstance(raw, dict) else {}

    def save_employer_state(self, state: dict[str, Any]) -> None:
        _atomic_write(self.employers_path, {k: v for k, v in (state or {}).items() if isinstance(v, dict)})

    def load_profile(self) -> dict[str, Any]:
        raw = _read_json(self.profile_path, {})
        return normalize_profile(raw if isinstance(raw, dict) else {})

    def save_profile(self, profile: dict[str, Any]) -> dict[str, Any]:
        incoming = dict(profile or {})
        incoming.pop("api_keys", None)
        current = normalize_profile({**self.load_profile(), **incoming})
        _atomic_write(self.profile_path, current)
        return current

    def load_secrets(self) -> dict[str, str]:
        raw = _read_json(self.secrets_path, {})
        if not isinstance(raw, dict):
            return {}
        out: dict[str, str] = {}
        for key, value in raw.items():
            name = str(key or "").strip().upper()
            text = str(value or "").strip()
            if name in CAREER_SECRET_NAMES and text:
                out[name] = text
        return out

    def save_secret(self, name: str, value: str) -> None:
        key = str(name or "").strip().upper()
        if key not in CAREER_SECRET_NAMES:
            raise CareerError(f"unknown career secret {name}", status=400)
        secrets_map = self.load_secrets()
        text = str(value or "").strip()
        if text:
            secrets_map[key] = text
        else:
            secrets_map.pop(key, None)
        self.secrets_path.parent.mkdir(parents=True, exist_ok=True)
        _atomic_write(self.secrets_path, secrets_map)
        try:
            os.chmod(self.secrets_path, 0o600)
        except OSError:
            pass

    def has_secret(self, name: str) -> bool:
        return bool(self.get_secret(name))

    def get_secret(self, name: str) -> str:
        return self.load_secrets().get(str(name or "").strip().upper(), "")

    def _load_rows(self, path: Path) -> list[dict[str, Any]]:
        raw = _read_json(path, [])
        if not isinstance(raw, list):
            return []
        return [row for row in raw if isinstance(row, dict) and row.get("id")]

    def load_opportunities(self) -> list[dict[str, Any]]:
        return self._load_rows(self.jobs_path)

    def save_opportunities(self, rows: list[dict[str, Any]]) -> None:
        _atomic_write(self.jobs_path, rows)

    def upsert_opportunities(self, incoming: list[dict[str, Any]]) -> list[dict[str, Any]]:
        existing = self.load_opportunities()
        by_id = {row["id"]: row for row in existing}
        by_url: dict[str, str] = {}
        for row in existing:
            key = normalize_job_url(str(row.get("url") or ""))
            if key:
                by_url[key] = str(row["id"])
        for row in incoming:
            if not isinstance(row, dict) or not row.get("id"):
                continue
            url_key = normalize_job_url(str(row.get("url") or ""))
            if url_key and url_key in by_url:
                row = {**row, "id": by_url[url_key]}
            prev = by_id.get(row["id"]) or {}
            merged = {**prev, **row}
            if prev.get("stage") in {"applied", "replied", "interview", "offer", "won", "rejected"}:
                merged["stage"] = prev["stage"]
            merged["favorite"] = bool(merged.get("favorite"))
            merged["archived"] = bool(merged.get("archived"))
            if not merged["archived"]:
                merged["archived_at"] = None
            if not merged.get("created_at"):
                merged["created_at"] = prev.get("created_at") or _now()
            merged["description"] = html_to_text(str(merged.get("description") or ""))[:4000]
            by_id[row["id"]] = merged
            if url_key:
                by_url[url_key] = str(merged["id"])
        rows = list(by_id.values())
        self.save_opportunities(rows)
        return rows

    def get_opportunity(self, oid: str) -> dict[str, Any]:
        for row in self.load_opportunities():
            if row.get("id") == oid:
                return row
        raise CareerError(f"opportunity {oid} not found", status=404)

    def update_opportunity(self, oid: str, patch: dict[str, Any]) -> dict[str, Any]:
        rows = self.load_opportunities()
        found = False
        for index, row in enumerate(rows):
            if row.get("id") != oid:
                continue
            merged = {**row, **patch, "id": oid, "updated_at": _now()}
            if merged.get("stage") and merged["stage"] not in STAGES:
                raise CareerError(f"unknown stage {merged['stage']}")
            merged["favorite"] = bool(merged.get("favorite"))
            merged["archived"] = bool(merged.get("archived"))
            if not merged["archived"]:
                merged["archived_at"] = None
            rows[index] = merged
            found = True
            break
        if not found:
            raise CareerError(f"opportunity {oid} not found", status=404)
        self.save_opportunities(rows)
        return next(row for row in rows if row["id"] == oid)

    def load_applications(self) -> list[dict[str, Any]]:
        return _dedupe_applications(self._load_rows(self.apps_path))

    def save_applications(self, rows: list[dict[str, Any]]) -> None:
        _atomic_write(self.apps_path, rows)

    def save_bytes(self, name: str, data: bytes, *, file_id: str = "") -> dict[str, Any]:
        fid = str(file_id or uuid.uuid4().hex[:10]).strip() or uuid.uuid4().hex[:10]
        filename = Path(str(name or "file").strip()).name or "file"
        safe = re.sub(r"[^A-Za-z0-9._-]+", "_", filename)[:80] or "file"
        self.files_dir.mkdir(parents=True, exist_ok=True)
        path = self.files_dir / f"{fid}_{safe}"
        path.write_bytes(data or b"")
        return {"file_id": fid, "name": filename, "path": path.name, "size": len(data or b"")}

    def read_bytes(self, file_id: str) -> dict[str, Any]:
        fid = str(file_id or "").strip()
        if not fid:
            raise CareerError("id is required")
        if self.files_dir.is_dir():
            for path in self.files_dir.iterdir():
                if path.is_file() and path.name.startswith(f"{fid}_"):
                    raw = path.read_bytes()
                    return {
                        "file_id": fid,
                        "name": path.name[len(fid) + 1 :] or path.name,
                        "path": path.name,
                        "size": len(raw),
                        "data": base64.b64encode(raw).decode("ascii"),
                    }
        raise CareerError(f"file {fid} not found", status=404)

    def upsert_application(self, row: dict[str, Any]) -> dict[str, Any]:
        rows = _dedupe_applications(self._load_rows(self.apps_path))
        oid = str(row.get("opportunity_id") or "")
        existing = next((item for item in rows if oid and item.get("opportunity_id") == oid), None)
        aid = str(row.get("id") or (existing or {}).get("id") or f"ap-{uuid.uuid4().hex[:10]}")
        now = _now()
        created = (existing or {}).get("created_at") or row.get("created_at") or now
        row = {**row, "id": aid, "created_at": created, "updated_at": now}
        by_id = {item["id"]: item for item in rows}
        if existing and existing.get("id") != aid:
            by_id.pop(str(existing.get("id")), None)
        by_id[aid] = {**(existing or {}), **row}
        cleaned = _dedupe_applications(list(by_id.values()))
        self.save_applications(cleaned)
        return next(item for item in cleaned if item["id"] == aid)

    def load_inbox(self) -> list[dict[str, Any]]:
        return self._load_rows(self.inbox_path)

    def save_inbox(self, rows: list[dict[str, Any]]) -> None:
        _atomic_write(self.inbox_path, rows)

    def add_inbox(self, row: dict[str, Any]) -> dict[str, Any]:
        rows = self.load_inbox()
        item = {
            **row,
            "id": str(row.get("id") or f"in-{uuid.uuid4().hex[:10]}"),
            "received_at": row.get("received_at") or _now(),
        }
        rows.insert(0, item)
        self.save_inbox(rows[:200])
        return item

    def load_loop(self) -> dict[str, Any]:
        raw = _read_json(self.loop_path, {})
        if not isinstance(raw, dict) or not raw:
            raw = {
                "enabled": False,
                "phase": "idle",
                "next_due": 0.0,
                "last_tick": 0.0,
                "last_result": "loop is paused - start it from the Career desk",
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
        """Start/stop/schedule written while a hunt holds the desk lock."""
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

    def load_journal(self) -> list[dict[str, Any]]:
        raw = _read_json(self.journal_path, [])
        return [row for row in raw if isinstance(row, dict)] if isinstance(raw, list) else []

    def append_journal(self, row: dict[str, Any]) -> None:
        items = self.load_journal()
        items.append({**row, "at": _now()})
        _atomic_write(self.journal_path, items[-200:])
