"""JSON store for Navin Tenders under the instance data dir."""

from __future__ import annotations

import base64
import json
import os
import re
import secrets
import tempfile
import time
import zipfile
from pathlib import Path
from typing import Any
from xml.etree import ElementTree

from navin.config.paths import get_runtime_subdir
from navin.tenders.errors import TenderError
from navin.tenders.normalize import SEND_MODES, STAGES
from navin.tenders.profile import (
    csv_list,
    default_templates,
    filed_documents,
    normalize_custom_sources,
    normalize_partners,
    normalize_references,
    normalize_sites,
    normalize_templates,
    wizard_ready,
)
from navin.tenders.sources import catalog as source_catalog

SCHEMA = 1
MAX_UPLOAD_BYTES = 8 * 1024 * 1024
EXCERPT_LIMIT = 12_000
UPLOAD_KINDS = {
    "word_template": (".docx",),
    "ppt_template": (".pptx",),
    "reuse_slide": (".docx", ".pptx", ".pdf"),
    "reference": (".docx", ".pptx", ".pdf"),
}


def default_channels() -> dict[str, Any]:
    return {
        "telegram": False,
        "whatsapp": False,
        "email": False,
        "teams": False,
        "slack": False,
        "telegram_to": "",
        "whatsapp_to": "",
        "email_to": "",
        "teams_to": "",
        "slack_to": "",
    }


def merge_channels(raw: Any) -> dict[str, Any]:
    base = default_channels()
    if not isinstance(raw, dict):
        return base
    for key in base:
        if key not in raw:
            continue
        if key.endswith("_to"):
            base[key] = str(raw.get(key) or "")
        else:
            base[key] = bool(raw.get(key))
    return base


def _now() -> float:
    return time.time()


def _atomic_write(path: Path, payload: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    text = json.dumps(payload, indent=2, ensure_ascii=False, sort_keys=True)
    fd, tmp = tempfile.mkstemp(prefix=".navin-tenders-", suffix=".tmp", dir=str(path.parent))
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


def default_profile() -> dict[str, Any]:
    return {
        "name": "",
        "legal_name": "",
        "specialty": "",
        "strengths": [],
        "country": "",
        "phone": "",
        "email": "",
        "website": "",
        "currency": "",
        "locale": "fr-FR",
        "crafts": [],
        "countries": [],
        "languages": [],
        "certifications": [],
        "tender_types": [],
        "project_types": [],
        "partners": [],
        "sites": [],
        "headcount": 0,
        "min_budget": 0,
        "max_budget": 0,
        "min_deadline_days": 10,
        "min_score": 70,
        "turnover": 0,
        "project_size_max": 0,
        "send_mode": "approval",
        "mail": {"gmail": False, "outlook": False, "watch": True},
        "channels": default_channels(),
        "enabled_sources": [],
        "source_ids": [],
        "custom_sources": [],
        "references": [],
        "documents": [],
        "templates": default_templates(),
        "team": [],
        "price_book": [],
        "methodology": "",
        "legal_clauses": "",
        "brief": "",
        "wizard_complete": False,
        "wizard_step": 1,
        "templates_skipped": False,
        "sites_skipped": False,
        "references_skipped": False,
        "channels_skipped": False,
        "archive_after_days": 45,
        "delete_after_days": 60,
    }


class TenderStore:
    """On-disk tender desk. One instance per Navin data directory."""

    def __init__(self, root: Path | str | None = None) -> None:
        self.root = Path(root) if root is not None else get_runtime_subdir("tenders")
        self.root.mkdir(parents=True, exist_ok=True)
        self.profile_path = self.root / "profile.json"
        self.tenders_path = self.root / "tenders.json"
        self.journal_path = self.root / "journal.json"
        self.loop_path = self.root / "loop.json"
        self.loop_intent_path = self.root / "loop.intent.json"
        self.discoveries_path = self.root / "discoveries.json"
        self.files_dir = self.root / "files"
        self.secrets_path = self.root / "secrets.json"

    def load_profile(self) -> dict[str, Any]:
        raw = _read_json(self.profile_path, {})
        if not isinstance(raw, dict):
            raw = {}
        base = default_profile()
        base.update(raw)
        return self._normalize_profile(base, incoming=raw, strict=False)

    def save_profile(self, profile: dict[str, Any]) -> dict[str, Any]:
        current = self.load_profile()
        incoming = dict(profile)
        if "channels" in incoming:
            previous = current.get("channels") if isinstance(current.get("channels"), dict) else {}
            incoming["channels"] = merge_channels({**previous, **incoming["channels"]})
        if "mail" in incoming and isinstance(incoming["mail"], dict):
            incoming["mail"] = {
                **(current.get("mail") if isinstance(current.get("mail"), dict) else {}),
                **incoming["mail"],
            }
        if "templates" in incoming and isinstance(incoming["templates"], dict):
            incoming["templates"] = normalize_templates(
                {**normalize_templates(current.get("templates")), **incoming["templates"]}
            )
        current.update(incoming)
        current = self._normalize_profile(current, incoming=incoming, strict=True)
        _atomic_write(self.profile_path, current)
        return current

    def _normalize_profile(
        self,
        current: dict[str, Any],
        *,
        incoming: dict[str, Any],
        strict: bool,
    ) -> dict[str, Any]:
        mode = str(current.get("send_mode") or "approval").strip().lower()
        if mode not in SEND_MODES:
            if strict and "send_mode" in incoming:
                raise TenderError("send_mode must be draft, approval or autonomous")
            mode = "approval"
        current["send_mode"] = mode
        current["channels"] = merge_channels(current.get("channels"))
        mail = current.get("mail") if isinstance(current.get("mail"), dict) else {}
        current["mail"] = {
            "gmail": bool(mail.get("gmail")),
            "outlook": bool(mail.get("outlook")),
            "watch": bool(mail.get("watch", True)),
        }
        for key in (
            "crafts",
            "strengths",
            "languages",
            "certifications",
            "tender_types",
            "project_types",
            "source_ids",
            "enabled_sources",
        ):
            current[key] = csv_list(current.get(key))
        current["countries"] = csv_list(current.get("countries"), upper=True)
        current["sites"] = normalize_sites(current.get("sites"))
        current["partners"] = normalize_partners(current.get("partners"))
        current["references"] = normalize_references(current.get("references"))
        current["custom_sources"] = normalize_custom_sources(current.get("custom_sources"))
        current["templates"] = normalize_templates(current.get("templates"))
        current["wizard_complete"] = bool(current.get("wizard_complete"))
        try:
            step = int(current.get("wizard_step") or 1)
        except (TypeError, ValueError):
            step = 1
        current["wizard_step"] = min(7, max(1, step))
        try:
            current["headcount"] = max(0, int(current.get("headcount") or 0))
        except (TypeError, ValueError):
            current["headcount"] = 0
        for key in ("min_budget", "max_budget", "turnover", "project_size_max"):
            try:
                current[key] = float(current.get(key) or 0)
            except (TypeError, ValueError):
                current[key] = 0
        for key in ("min_deadline_days", "min_score"):
            try:
                current[key] = int(current.get(key) or 0)
            except (TypeError, ValueError):
                current[key] = 0
        from navin.tenders.retention import ARCHIVE_AFTER_DAYS, DELETE_AFTER_DAYS, retention_days

        if "archive_after_days" not in current:
            current["archive_after_days"] = ARCHIVE_AFTER_DAYS
        if "delete_after_days" not in current:
            current["delete_after_days"] = DELETE_AFTER_DAYS
        archive_after, delete_after = retention_days(current)
        current["archive_after_days"] = archive_after
        current["delete_after_days"] = delete_after
        for key in ("templates_skipped", "sites_skipped", "references_skipped", "channels_skipped"):
            current[key] = bool(current.get(key))
        current["country"] = str(current.get("country") or "").strip().upper()
        current["currency"] = str(current.get("currency") or "").strip().upper()
        current["brief"] = str(current.get("brief") or "").strip()
        current["methodology"] = str(current.get("methodology") or "")
        current["legal_clauses"] = str(current.get("legal_clauses") or "")
        for key in ("team", "price_book"):
            rows = current.get(key)
            if not isinstance(rows, list):
                current[key] = []
                continue
            kept: list[Any] = []
            for row in rows:
                if isinstance(row, dict):
                    kept.append(dict(row))
                elif str(row).strip():
                    kept.append(str(row).strip())
            current[key] = kept
        return current

    def wizard_ready(self) -> bool:
        return wizard_ready(self.load_profile())

    def load_secrets(self) -> dict[str, str]:
        raw = _read_json(self.secrets_path, {})
        if not isinstance(raw, dict):
            return {}
        return {str(key): str(value) for key, value in raw.items() if str(value).strip()}

    def save_secret(self, name: str, value: str) -> None:
        key = str(name or "").strip()
        if not key:
            raise TenderError("secret name is required")
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
        return bool(self.load_secrets().get(str(name or "").strip()))

    def get_secret(self, name: str) -> str:
        return self.load_secrets().get(str(name or "").strip(), "")

    def save_upload(
        self,
        *,
        kind: str,
        name: str,
        data: str,
        meta: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        kind_key = str(kind or "").strip().lower()
        allowed = UPLOAD_KINDS.get(kind_key)
        if not allowed:
            raise TenderError("upload kind must be word_template, ppt_template, reuse_slide or reference")
        filename = Path(str(name or "file").strip()).name or "file"
        suffix = Path(filename).suffix.lower()
        if suffix not in allowed:
            raise TenderError(f"{filename} must be one of {', '.join(allowed)}")
        raw = _decode_upload(data)
        if len(raw) > MAX_UPLOAD_BYTES:
            raise TenderError(f"file larger than {MAX_UPLOAD_BYTES // (1024 * 1024)} MB")
        if not raw:
            raise TenderError("empty file")
        file_id = secrets.token_hex(8)
        safe = re.sub(r"[^A-Za-z0-9._-]+", "_", filename)[:80] or "file"
        self.files_dir.mkdir(parents=True, exist_ok=True)
        path = self.files_dir / f"{file_id}_{safe}"
        path.write_bytes(raw)
        excerpt = extract_office_text(path)
        extract_name = f"{file_id}.txt"
        (self.files_dir / extract_name).write_text(excerpt, encoding="utf-8")
        record = {
            "file_id": file_id,
            "kind": kind_key,
            "name": filename,
            "path": str(path.name),
            "extract": extract_name,
            "size": len(raw),
            "chars": len(excerpt),
            "excerpt": excerpt[:EXCERPT_LIMIT],
            **{key: value for key, value in (meta or {}).items() if key not in {"file_id", "path", "extract"}},
        }
        profile = self.load_profile()
        templates = normalize_templates(profile.get("templates"))
        if kind_key == "word_template":
            templates["word"] = list(templates.get("word") or []) + [record]
        elif kind_key == "ppt_template":
            templates["ppt"] = list(templates.get("ppt") or []) + [record]
        elif kind_key == "reuse_slide":
            templates["reuse_slides"] = list(templates.get("reuse_slides") or []) + [record]
        else:
            refs = list(profile.get("references") or [])
            refs.append(
                {
                    "title": str((meta or {}).get("title") or Path(filename).stem),
                    "client": str((meta or {}).get("client") or ""),
                    "year": str((meta or {}).get("year") or ""),
                    "country": str((meta or {}).get("country") or "").upper(),
                    "amount": (meta or {}).get("amount"),
                    "file_id": file_id,
                    "name": filename,
                    "path": str(path.name),
                    "extract": extract_name,
                    "chars": len(excerpt),
                    "excerpt": excerpt[:EXCERPT_LIMIT],
                }
            )
            profile["references"] = refs
        profile["templates"] = templates
        self.save_profile(profile)
        return record

    def add_reference(self, meta: dict[str, Any] | None = None) -> dict[str, Any]:
        data = meta if isinstance(meta, dict) else {}
        title = str(data.get("title") or data.get("name") or "").strip()
        if not title:
            raise TenderError("title is required")
        record = {
            "title": title,
            "client": str(data.get("client") or "").strip(),
            "year": str(data.get("year") or "").strip(),
            "country": str(data.get("country") or "").strip().upper(),
            "amount": data.get("amount"),
            "file_id": "",
            "name": "",
            "excerpt": str(data.get("excerpt") or "").strip(),
        }
        profile = self.load_profile()
        refs = list(profile.get("references") or [])
        refs.append(record)
        profile["references"] = refs
        self.save_profile(profile)
        return record

    def remove_file(self, file_id: str = "", title: str = "") -> dict[str, Any]:
        fid = str(file_id or "").strip()
        title_key = str(title or "").strip().lower()
        if not fid and not title_key:
            raise TenderError("id or title is required")
        profile = self.load_profile()
        templates = normalize_templates(profile.get("templates"))
        removed: dict[str, Any] | None = None

        def keep(row: Any) -> bool:
            nonlocal removed
            if not isinstance(row, dict):
                return False
            row_id = str(row.get("file_id") or "").strip()
            row_title = str(row.get("title") or row.get("name") or "").strip().lower()
            hit = (fid and row_id == fid) or (not fid and title_key and row_title == title_key and not row_id)
            if hit and removed is None:
                removed = dict(row)
                return False
            return True

        for key in ("word", "ppt", "reuse_slides"):
            templates[key] = [row for row in (templates.get(key) or []) if keep(row)]
        profile["templates"] = templates
        profile["references"] = [row for row in (profile.get("references") or []) if keep(row)]
        if removed is None:
            raise TenderError("file not found", status=404)
        self.save_profile(profile)
        for name in (removed.get("path"), removed.get("extract"), f"{removed.get('file_id')}.txt"):
            token = str(name or "").strip()
            if not token:
                continue
            path = self.files_dir / Path(token).name
            try:
                path.unlink()
            except OSError:
                pass
        return removed

    def save_bytes(self, name: str, data: bytes, *, file_id: str = "") -> dict[str, Any]:
        fid = str(file_id or secrets.token_hex(8)).strip() or secrets.token_hex(8)
        filename = Path(str(name or "file").strip()).name or "file"
        safe = re.sub(r"[^A-Za-z0-9._-]+", "_", filename)[:80] or "file"
        self.files_dir.mkdir(parents=True, exist_ok=True)
        path = self.files_dir / f"{fid}_{safe}"
        path.write_bytes(data or b"")
        return {
            "file_id": fid,
            "name": filename,
            "path": path.name,
            "size": len(data or b""),
        }

    def read_bytes(self, file_id: str) -> dict[str, Any]:
        fid = str(file_id or "").strip()
        if not fid:
            raise TenderError("id is required")
        if self.files_dir.is_dir():
            for path in self.files_dir.iterdir():
                if path.is_file() and path.name.startswith(f"{fid}_"):
                    raw = path.read_bytes()
                    name = path.name[len(fid) + 1 :] or path.name
                    return {
                        "file_id": fid,
                        "name": name,
                        "path": path.name,
                        "size": len(raw),
                        "data": base64.b64encode(raw).decode("ascii"),
                    }
        raise TenderError(f"file {fid} not found", status=404)

    def read_upload(self, file_id: str) -> dict[str, Any]:
        fid = str(file_id or "").strip()
        if not fid:
            raise TenderError("id is required")
        for row in filed_documents(self.load_profile()):
            if row.get("file_id") != fid:
                continue
            text = ""
            extract = self.files_dir / Path(str(row.get("extract") or f"{fid}.txt")).name
            if extract.is_file():
                text = extract.read_text(encoding="utf-8")
            elif row.get("path"):
                text = extract_office_text(self.files_dir / Path(str(row["path"])).name)
            else:
                text = str(row.get("excerpt") or "")
            return {**row, "text": text, "chars": len(text)}
        raise TenderError(f"file {fid} not found", status=404)

    def add_custom_source(self, row: dict[str, Any]) -> dict[str, Any]:
        profile = self.load_profile()
        sources = normalize_custom_sources(profile.get("custom_sources"))
        incoming = normalize_custom_sources([row])
        if not incoming:
            raise TenderError("name and url are required")
        item = incoming[0]
        sources = [src for src in sources if src["id"] != item["id"]]
        sources.append(item)
        profile["custom_sources"] = sources
        self.save_profile(profile)
        key = str(row.get("api_key") or row.get("key") or "").strip()
        if key:
            self.save_secret(f"custom:{item['id']}", key)
        return item

    def remove_custom_source(self, source_id: str) -> None:
        sid = str(source_id or "").strip()
        if not sid:
            raise TenderError("id is required")
        profile = self.load_profile()
        profile["custom_sources"] = [
            row for row in normalize_custom_sources(profile.get("custom_sources")) if row["id"] != sid
        ]
        self.save_profile(profile)
        secrets_map = self.load_secrets()
        if f"custom:{sid}" in secrets_map:
            self.save_secret(f"custom:{sid}", "")

    def load_tenders(self) -> list[dict[str, Any]]:
        raw = _read_json(self.tenders_path, [])
        if not isinstance(raw, list):
            return []
        rows: list[dict[str, Any]] = []
        for row in raw:
            if not isinstance(row, dict) or not row.get("id"):
                continue
            row["favorite"] = bool(row.get("favorite"))
            row["archived"] = bool(row.get("archived"))
            if not row["archived"]:
                row["archived_at"] = None
            rows.append(row)
        return rows

    def save_tenders(self, rows: list[dict[str, Any]]) -> None:
        _atomic_write(self.tenders_path, rows)

    def upsert_tenders(self, incoming: list[dict[str, Any]]) -> list[dict[str, Any]]:
        by_id = {row["id"]: row for row in self.load_tenders()}
        now = _now()
        for row in incoming:
            tid = row.get("id")
            if not tid:
                continue
            existing = by_id.get(tid)
            if existing is None:
                by_id[tid] = row
                continue
            changed = False
            for key in ("deadline", "title", "description", "status", "budget"):
                if row.get(key) and row.get(key) != existing.get(key):
                    existing[key] = row[key]
                    changed = True
            if changed:
                existing.setdefault("history", []).append(
                    {"t": now, "event": "notice-updated"}
                )
                existing["updated_at"] = now
            by_id[tid] = existing
        rows = sorted(by_id.values(), key=lambda r: float(r.get("updated_at") or 0), reverse=True)
        self.save_tenders(rows)
        return rows

    def get(self, tender_id: str) -> dict[str, Any]:
        for row in self.load_tenders():
            if row.get("id") == tender_id:
                return row
        raise TenderError(f"tender {tender_id} not found", status=404)

    def patch(self, tender_id: str, updates: dict[str, Any]) -> dict[str, Any]:
        rows = self.load_tenders()
        found = False
        for row in rows:
            if row.get("id") != tender_id:
                continue
            found = True
            stage = updates.get("stage")
            if stage is not None:
                if stage not in STAGES:
                    raise TenderError(f"unknown stage {stage}")
                row["stage"] = stage
            for key, value in updates.items():
                if key in {"id", "source_id"}:
                    continue
                row[key] = value
            row["updated_at"] = _now()
            row.setdefault("history", []).append({"t": row["updated_at"], "event": "patch"})
            break
        if not found:
            raise TenderError(f"tender {tender_id} not found", status=404)
        self.save_tenders(rows)
        return self.get(tender_id)

    def append_journal(self, event: dict[str, Any]) -> None:
        raw = _read_json(self.journal_path, [])
        if not isinstance(raw, list):
            raw = []
        event = dict(event)
        event.setdefault("t", _now())
        raw.append(event)
        _atomic_write(self.journal_path, raw[-400:])

    def load_journal(self) -> list[dict[str, Any]]:
        raw = _read_json(self.journal_path, [])
        return raw if isinstance(raw, list) else []

    def load_loop(self) -> dict[str, Any]:
        raw = _read_json(self.loop_path, {})
        default = {
            "enabled": False,
            "phase": "idle",
            "next_due": 0.0,
            "last_tick": 0.0,
            "last_result": "loop is paused - start it from the Tenders desk",
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
        if not isinstance(raw, dict) or not raw:
            self.save_loop(default)
            return default
        for key in ("next_due", "last_tick", "last_watch", "hunt_started_at"):
            if key in raw:
                try:
                    raw[key] = float(raw[key] or 0)
                except (TypeError, ValueError):
                    raw[key] = 0.0
        if raw.get("phase") is not None:
            raw["phase"] = str(raw.get("phase") or "idle")
        if "enabled" in raw:
            raw["enabled"] = bool(raw.get("enabled"))
        return raw

    def save_loop(self, data: dict[str, Any]) -> dict[str, Any]:
        data = dict(data)
        data["updated_at"] = _now()
        _atomic_write(self.loop_path, data)
        return data

    def load_loop_intent(self) -> dict[str, Any]:
        """Start/stop/schedule written while a collect holds the desk lock."""
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

    def load_discoveries(self) -> list[dict[str, Any]]:
        raw = _read_json(self.discoveries_path, [])
        return raw if isinstance(raw, list) else []

    def merge_discoveries(self, incoming: list[dict[str, Any]]) -> list[dict[str, Any]]:
        by_host = {str(row.get("host") or ""): dict(row) for row in self.load_discoveries() if row.get("host")}
        for row in incoming:
            host = str(row.get("host") or "")
            if not host:
                continue
            current = by_host.get(host) or {"host": host, "hits": 0, "url": row.get("url"), "added": False}
            current["hits"] = int(current.get("hits") or 0) + int(row.get("hits") or 1)
            current["url"] = row.get("url") or current.get("url")
            current["last_title"] = row.get("title") or current.get("last_title")
            current["updated_at"] = _now()
            by_host[host] = current
        rows = list(by_host.values())
        _atomic_write(self.discoveries_path, rows)
        return rows

    def accept_discovery(self, host: str) -> list[dict[str, Any]]:
        rows = self.load_discoveries()
        for row in rows:
            if row.get("host") == host:
                row["added"] = True
        _atomic_write(self.discoveries_path, rows)
        profile = self.load_profile()
        extra = list(profile.get("enabled_sources") or [])
        if host not in extra:
            extra.append(host)
            profile["enabled_sources"] = extra
            self.save_profile(profile)
        return rows

    def sources(self) -> list[dict[str, Any]]:
        profile = self.load_profile()
        picked = {str(item).strip() for item in (profile.get("source_ids") or []) if str(item).strip()}
        extra = {str(item).strip() for item in (profile.get("enabled_sources") or []) if str(item).strip()}
        rows = source_catalog()
        for row in rows:
            sid = str(row.get("id") or "")
            row["user_enabled"] = (not picked) or sid in picked or sid in extra
        return rows


def _decode_upload(data: str) -> bytes:
    text = str(data or "").strip()
    if not text:
        return b""
    if "," in text and text.lower().startswith("data:"):
        text = text.split(",", 1)[1]
    try:
        return base64.b64decode(text, validate=False)
    except (ValueError, TypeError) as exc:
        raise TenderError("invalid file encoding") from exc


def extract_office_text(path: Path) -> str:
    suffix = path.suffix.lower()
    if suffix == ".pdf":
        try:
            from pypdf import PdfReader

            reader = PdfReader(str(path), strict=False)
            chunks: list[str] = []
            for page in reader.pages[:40]:
                text = page.extract_text() or ""
                if text.strip():
                    chunks.append(text)
            return " ".join(chunks)
        except Exception:
            return ""
    if suffix not in {".docx", ".pptx"}:
        return ""
    try:
        with zipfile.ZipFile(path) as archive:
            names = archive.namelist()
            if suffix == ".docx":
                targets = [name for name in names if name == "word/document.xml"]
            else:
                targets = sorted(name for name in names if name.startswith("ppt/slides/slide") and name.endswith(".xml"))
            chunks: list[str] = []
            for name in targets[:40]:
                xml = archive.read(name)
                root = ElementTree.fromstring(xml)
                texts: list[str] = []
                for node in root.iter():
                    if node.text and node.text.strip():
                        texts.append(node.text.strip())
                    if node.tail and node.tail.strip():
                        texts.append(node.tail.strip())
                if texts:
                    chunks.append(" ".join(texts))
            return " ".join(chunks)
    except (OSError, zipfile.BadZipFile, ElementTree.ParseError):
        return ""
