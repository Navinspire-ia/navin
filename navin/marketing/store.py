# Copyright (c) 2026-present Navinspire IA
# SPDX-License-Identifier: AGPL-3.0-only

"""JSON store for the Marketing Agent OS under the instance data dir."""

from __future__ import annotations

import json
import os
import tempfile
import time
import uuid
from pathlib import Path
from typing import Any

from navin.config.paths import get_runtime_subdir
from navin.marketing.errors import MarketingError

SCHEMA = 1
CHANNELS = (
    "linkedin",
    "x",
    "instagram",
    "facebook",
    "tiktok",
    "youtube",
    "blog",
    "email",
    "reddit",
    "producthunt",
    "telegram",
)
CREATIVE_KINDS = ("image", "video", "audio", "banner")
CAMPAIGN_STATUSES = ("draft", "planned", "approved", "running", "paused", "done")
CONTENT_STATUSES = ("draft", "ready", "approved", "scheduled", "publishing", "published", "failed", "winner", "retired")
# Channels the desk can post to through an API or a local sink. The others
# (youtube, producthunt) stay copy-and-paste or use a configured bridge.
PUBLISH_CHANNELS = ("linkedin", "x", "facebook", "instagram", "tiktok", "reddit", "telegram", "email", "webhook", "blog")
SECRET_NAMES: dict[str, tuple[str, ...]] = {
    "linkedin": ("linkedin_token",),
    "x": ("x_api_key", "x_api_secret", "x_access_token", "x_access_secret"),
    "facebook": ("facebook_page_token",),
    "instagram": ("instagram_access_token",),
    "tiktok": ("tiktok_access_token",),
    "reddit": ("reddit_access_token",),
    "telegram": ("telegram_bot_token",),
    "email": (),
    "webhook": ("webhook_secret",),
    "blog": (),
    "plausible": ("plausible_key",),
    "matomo": ("matomo_token",),
    "social_oauth": (
        "linkedin_client_secret", "linkedin_refresh_token",
        "facebook_client_secret", "facebook_user_token",
        "instagram_client_secret", "instagram_refresh_token",
        "tiktok_client_secret", "tiktok_refresh_token",
        "reddit_client_secret", "reddit_refresh_token",
    ),
}


def _now() -> float:
    return time.time()


def _atomic_write(path: Path, payload: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    text = json.dumps(payload, indent=2, ensure_ascii=False, sort_keys=True)
    fd, tmp = tempfile.mkstemp(prefix=".navin-marketing-", suffix=".tmp", dir=str(path.parent))
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


def _new_id(prefix: str) -> str:
    return f"{prefix}-{uuid.uuid4().hex[:10]}"


STUCK_PHASES = frozenset({"publish", "measure", "learn", "create", "busy"})
STUCK_PHASE_S = 120.0


def _safe_float(value: Any, default: float = 0.0) -> float:
    try:
        return float(value or 0)
    except (TypeError, ValueError):
        return default


def heal_loop_state(raw: dict[str, Any], *, now: float | None = None) -> dict[str, Any]:
    """Repair corrupt timestamps and a cycle left mid-flight after a crash."""
    row = dict(raw)
    row["next_due"] = _safe_float(row.get("next_due"))
    row["last_tick"] = _safe_float(row.get("last_tick"))
    row["last_watch"] = _safe_float(row.get("last_watch"))
    row["enabled"] = bool(row.get("enabled"))
    try:
        row["cycle"] = max(0, int(row.get("cycle") or 0))
    except (TypeError, ValueError):
        row["cycle"] = 0
    phase = str(row.get("phase") or "idle").strip().lower() or "idle"
    if phase in STUCK_PHASES:
        stamp = max(row["last_tick"], _safe_float(row.get("updated_at")))
        clock = now if now is not None else _now()
        if not stamp or (clock - stamp) >= STUCK_PHASE_S:
            row["phase"] = "idle" if row["enabled"] else "paused"
            row["skipped_reason"] = "recovered_stuck_phase"
            row["last_result"] = "recovered stuck cycle - next tick will run"
        else:
            row["phase"] = phase
    else:
        row["phase"] = phase
    return row


def _loop_needs_persist(raw: dict[str, Any], healed: dict[str, Any]) -> bool:
    if healed.get("skipped_reason") == "recovered_stuck_phase" and healed.get(
        "phase"
    ) != raw.get("phase"):
        return True
    for key in ("next_due", "last_tick", "last_watch"):
        try:
            float(raw.get(key) or 0)
        except (TypeError, ValueError):
            return True
    try:
        int(raw.get("cycle") or 0)
    except (TypeError, ValueError):
        return True
    return False


def default_brand() -> dict[str, Any]:
    return {
        "schema": SCHEMA,
        "company": "",
        "product": "",
        "site": "",
        "description": "",
        "tone": "clear and confident",
        "colors": [],
        "fonts": [],
        "logo": "",
        "audience": "",
        "forbidden": [],
        "competitors": [],
        "liked_examples": [],
        "languages": ["fr", "en"],
        "countries": [],
        "industries": [],
        "updated_at": 0.0,
    }


def default_product() -> dict[str, Any]:
    return {
        "schema": SCHEMA,
        "name": "",
        "one_liner": "",
        "category": "",
        "pain": "",
        "value_prop": "",
        "audience": "",
        "search_terms": [],
        "brief_model": "",
        "stack": [],
        "workspace": "",
        "site": "",
        "source_kind": "",
        "docs": [],
        "screenshots": [],
        "updated_at": 0.0,
    }


def default_positioning() -> dict[str, Any]:
    return {
        "schema": SCHEMA,
        "icp": "",
        "personas": [],
        "pain": "",
        "value_prop": "",
        "differentiation": "",
        "statement": "",
        "updated_at": 0.0,
    }


def default_research() -> dict[str, Any]:
    return {
        "schema": SCHEMA,
        "market": "",
        "trends": [],
        "keywords": [],
        "competitors": [],
        "pricing_notes": [],
        "sources": [],
        "updated_at": 0.0,
    }


def default_analytics() -> dict[str, Any]:
    return {
        "schema": SCHEMA,
        "traffic": 0,
        "leads": 0,
        "signups": 0,
        "customers": 0,
        "revenue": 0.0,
        "cac": 0.0,
        "by_content": {},
        "updated_at": 0.0,
    }


def default_seo() -> dict[str, Any]:
    return {
        "schema": SCHEMA,
        "keywords": [],
        "pages": [],
        "rankings": [],
        "updated_at": 0.0,
    }


def default_ads() -> dict[str, Any]:
    return {
        "schema": SCHEMA,
        "campaigns": [],
        "spend": 0.0,
        "updated_at": 0.0,
    }


def default_harvest() -> dict[str, Any]:
    return {
        "schema": SCHEMA,
        "site": "",
        "name": "",
        "one_liner": "",
        "description": "",
        "logo": "",
        "og_image": "",
        "images": [],
        "colors": [],
        "fonts": [],
        "headings": [],
        "ctas": [],
        "pages": [],
        "social": {},
        "keywords": [],
        "updated_at": 0.0,
    }


def default_social() -> dict[str, Any]:
    return {
        "schema": SCHEMA,
        "posts": [],
        "updated_at": 0.0,
    }


def default_publish() -> dict[str, Any]:
    """Per-channel connector settings. Keys live in ``secrets.json``, not here."""
    return {
        "linkedin": {"enabled": False, "author": ""},
        "x": {"enabled": False},
        "facebook": {"enabled": False, "page_id": "", "api_version": "v23.0"},
        "instagram": {"enabled": False, "instagram_user_id": "", "auth_mode": "instagram", "api_version": "v23.0"},
        "tiktok": {"enabled": False},
        "reddit": {"enabled": False, "subreddit": "", "user_agent": "", "flair_id": ""},
        "telegram": {"enabled": False, "chat_id": ""},
        "email": {"enabled": False, "to": ""},
        "webhook": {"enabled": False, "url": ""},
        "blog": {"enabled": False, "dir": "", "base_url": ""},
        "per_cycle": 1,
    }


def default_analytics_source() -> dict[str, Any]:
    """Where measured traffic comes from: none, Plausible or Matomo. ``goal`` is the signup goal name/id."""
    return {"provider": "", "site_id": "", "base_url": "", "goal": "", "measured_at": 0.0, "last_error": ""}


def default_alert_channels() -> dict[str, Any]:
    return {
        "telegram": False,
        "whatsapp": False,
        "email": False,
        "telegram_to": "",
        "whatsapp_to": "",
        "email_to": "",
    }


def default_settings() -> dict[str, Any]:
    return {
        "schema": SCHEMA,
        "execution_mode": "approval",
        "auto_publish": False,
        "ai_assist": True,
        "winner_multiple": 2.0,
        "publish": default_publish(),
        "analytics": default_analytics_source(),
        "channels": default_alert_channels(),
        "utm_campaign": "",
        "media_base_url": "",
        "created_at": _now(),
    }


_PUBLISH_META = ("tested_at", "last_error", "account")


def normalize_publish(raw: Any) -> dict[str, Any]:
    base = default_publish()
    incoming = raw if isinstance(raw, dict) else {}
    for channel, defaults in base.items():
        if channel == "per_cycle":
            continue
        patch = incoming.get(channel) if isinstance(incoming.get(channel), dict) else {}
        allowed = set(defaults) | {"enabled", *_PUBLISH_META}
        merged = {**defaults, **{key: value for key, value in patch.items() if key in allowed}}
        merged["enabled"] = bool(merged.get("enabled"))
        for key, value in list(merged.items()):
            if key == "tested_at":
                try:
                    merged[key] = float(value or 0)
                except (TypeError, ValueError):
                    merged[key] = 0.0
            elif key != "enabled":
                merged[key] = str(value or "").strip()
        base[channel] = merged
    try:
        base["per_cycle"] = max(0, min(10, int(incoming.get("per_cycle", base["per_cycle"]))))
    except (TypeError, ValueError):
        base["per_cycle"] = 1
    return base


def normalize_analytics_source(raw: Any) -> dict[str, Any]:
    base = default_analytics_source()
    incoming = raw if isinstance(raw, dict) else {}
    provider = str(incoming.get("provider") or "").strip().lower()
    base["provider"] = provider if provider in {"plausible", "matomo"} else ""
    base["site_id"] = str(incoming.get("site_id") or "").strip()
    base["base_url"] = str(incoming.get("base_url") or "").strip().rstrip("/")
    base["goal"] = str(incoming.get("goal") or "").strip()
    try:
        base["measured_at"] = float(incoming.get("measured_at") or 0)
    except (TypeError, ValueError):
        base["measured_at"] = 0.0
    base["last_error"] = str(incoming.get("last_error") or "").strip()
    return base


def normalize_alert_channels(raw: Any) -> dict[str, Any]:
    base = default_alert_channels()
    incoming = raw if isinstance(raw, dict) else {}
    for key in ("telegram", "whatsapp", "email"):
        if key in incoming:
            base[key] = bool(incoming.get(key))
    for key in ("telegram_to", "whatsapp_to", "email_to"):
        if key in incoming:
            base[key] = str(incoming.get(key) or "").strip()
    return base


class MarketingStore:
    """On-disk marketing book. One instance per Navin data directory."""

    def __init__(self, root: Path | None = None) -> None:
        self.root = Path(root) if root is not None else get_runtime_subdir("marketing")
        self.root.mkdir(parents=True, exist_ok=True)

    def path(self, name: str) -> Path:
        return self.root / name

    def _load_object(self, name: str, fallback: dict[str, Any]) -> dict[str, Any]:
        raw = _read_json(self.path(name), {})
        if not isinstance(raw, dict) or not raw:
            return dict(fallback)
        merged = {**fallback, **raw}
        merged["schema"] = SCHEMA
        return merged

    def _save_object(self, name: str, data: dict[str, Any]) -> dict[str, Any]:
        row = dict(data)
        row["schema"] = SCHEMA
        row["updated_at"] = _now()
        _atomic_write(self.path(name), row)
        return row

    def _load_rows(self, name: str) -> list[dict[str, Any]]:
        raw = _read_json(self.path(name), [])
        if not isinstance(raw, list):
            return []
        return [row for row in raw if isinstance(row, dict)]

    def _save_rows(self, name: str, rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
        cleaned = [row for row in rows if isinstance(row, dict)]
        _atomic_write(self.path(name), cleaned)
        return cleaned

    def load_brand(self) -> dict[str, Any]:
        row = self._load_object("brand.json", default_brand())
        row["colors"] = _split(row.get("colors"))
        row["fonts"] = _split(row.get("fonts"))
        row["forbidden"] = _split(row.get("forbidden"))
        row["competitors"] = _split(row.get("competitors"))
        row["liked_examples"] = _split(row.get("liked_examples"))
        row["languages"] = _split(row.get("languages")) or ["fr", "en"]
        row["countries"] = _split(row.get("countries"))
        row["industries"] = _split(row.get("industries"))
        return row

    def save_brand(self, data: dict[str, Any]) -> dict[str, Any]:
        current = self.load_brand()
        merged = {**current, **data}
        merged["colors"] = _split(merged.get("colors"))
        merged["fonts"] = _split(merged.get("fonts"))
        merged["forbidden"] = _split(merged.get("forbidden"))
        merged["competitors"] = _split(merged.get("competitors"))
        merged["liked_examples"] = _split(merged.get("liked_examples"))
        merged["languages"] = _split(merged.get("languages")) or ["fr", "en"]
        merged["countries"] = _split(merged.get("countries"))
        merged["industries"] = _split(merged.get("industries"))
        return self._save_object("brand.json", merged)

    def load_product(self) -> dict[str, Any]:
        row = self._load_object("product.json", default_product())
        row["stack"] = _split(row.get("stack"))
        row["docs"] = _split(row.get("docs"))
        row["screenshots"] = _split(row.get("screenshots"))
        return row

    def save_product(self, data: dict[str, Any]) -> dict[str, Any]:
        current = self.load_product()
        merged = {**current, **data}
        merged["stack"] = _split(merged.get("stack"))
        merged["docs"] = _split(merged.get("docs"))
        merged["screenshots"] = _split(merged.get("screenshots"))
        return self._save_object("product.json", merged)

    def load_positioning(self) -> dict[str, Any]:
        row = self._load_object("positioning.json", default_positioning())
        personas = row.get("personas")
        row["personas"] = (
            [str(item).strip() for item in personas if str(item).strip()]
            if isinstance(personas, list)
            else _split(personas)
        )
        return row

    def save_positioning(self, data: dict[str, Any]) -> dict[str, Any]:
        current = self.load_positioning()
        merged = {**current, **data}
        personas = merged.get("personas")
        merged["personas"] = (
            [str(item).strip() for item in personas if str(item).strip()]
            if isinstance(personas, list)
            else _split(personas)
        )
        return self._save_object("positioning.json", merged)

    def load_research(self) -> dict[str, Any]:
        row = self._load_object("research.json", default_research())
        for key in ("trends", "keywords", "pricing_notes", "sources"):
            row[key] = _split(row.get(key))
        competitors = row.get("competitors")
        if not isinstance(competitors, list):
            row["competitors"] = []
        return row

    def save_research(self, data: dict[str, Any]) -> dict[str, Any]:
        current = self.load_research()
        merged = {**current, **data}
        for key in ("trends", "keywords", "pricing_notes", "sources"):
            merged[key] = _split(merged.get(key))
        if not isinstance(merged.get("competitors"), list):
            merged["competitors"] = current.get("competitors") or []
        return self._save_object("research.json", merged)

    def load_analytics(self) -> dict[str, Any]:
        row = self._load_object("analytics.json", default_analytics())
        by_content = row.get("by_content")
        row["by_content"] = by_content if isinstance(by_content, dict) else {}
        return row

    def save_analytics(self, data: dict[str, Any]) -> dict[str, Any]:
        current = self.load_analytics()
        merged = {**current, **data}
        if not isinstance(merged.get("by_content"), dict):
            merged["by_content"] = current.get("by_content") or {}
        return self._save_object("analytics.json", merged)

    def load_seo(self) -> dict[str, Any]:
        row = self._load_object("seo.json", default_seo())
        for key in ("keywords", "pages", "rankings"):
            if not isinstance(row.get(key), list):
                row[key] = []
        return row

    def save_seo(self, data: dict[str, Any]) -> dict[str, Any]:
        current = self.load_seo()
        merged = {**current, **data}
        for key in ("keywords", "pages", "rankings"):
            if not isinstance(merged.get(key), list):
                merged[key] = current.get(key) or []
        return self._save_object("seo.json", merged)

    def load_ads(self) -> dict[str, Any]:
        row = self._load_object("ads.json", default_ads())
        if not isinstance(row.get("campaigns"), list):
            row["campaigns"] = []
        return row

    def save_ads(self, data: dict[str, Any]) -> dict[str, Any]:
        current = self.load_ads()
        merged = {**current, **data}
        if not isinstance(merged.get("campaigns"), list):
            merged["campaigns"] = current.get("campaigns") or []
        merged["spend"] = 0.0
        return self._save_object("ads.json", merged)

    def load_harvest(self) -> dict[str, Any]:
        row = self._load_object("harvest.json", default_harvest())
        for key in ("images", "colors", "fonts", "headings", "ctas", "pages", "keywords"):
            if not isinstance(row.get(key), list):
                row[key] = []
        if not isinstance(row.get("social"), dict):
            row["social"] = {}
        return row

    def save_harvest(self, data: dict[str, Any]) -> dict[str, Any]:
        current = self.load_harvest()
        merged = {**current, **data}
        for key in ("images", "colors", "fonts", "headings", "ctas", "pages", "keywords"):
            if not isinstance(merged.get(key), list):
                merged[key] = current.get(key) or []
        if not isinstance(merged.get("social"), dict):
            merged["social"] = current.get("social") or {}
        return self._save_object("harvest.json", merged)

    def load_social(self) -> dict[str, Any]:
        row = self._load_object("social.json", default_social())
        if not isinstance(row.get("posts"), list):
            row["posts"] = []
        return row

    def save_social(self, data: dict[str, Any]) -> dict[str, Any]:
        current = self.load_social()
        merged = {**current, **data}
        if not isinstance(merged.get("posts"), list):
            merged["posts"] = current.get("posts") or []
        return self._save_object("social.json", merged)

    def load_settings(self) -> dict[str, Any]:
        row = self._load_object("settings.json", default_settings())
        mode = str(row.get("execution_mode") or "approval").strip().lower()
        if mode not in {"autonomous", "approval"}:
            mode = "approval"
        row["execution_mode"] = mode
        row["auto_publish"] = bool(row.get("auto_publish"))
        row["ai_assist"] = row.get("ai_assist") is not False
        try:
            row["winner_multiple"] = max(1.1, float(row.get("winner_multiple") or 2.0))
        except (TypeError, ValueError):
            row["winner_multiple"] = 2.0
        row["publish"] = normalize_publish(row.get("publish"))
        row["analytics"] = normalize_analytics_source(row.get("analytics"))
        row["channels"] = normalize_alert_channels(row.get("channels"))
        row["utm_campaign"] = str(row.get("utm_campaign") or "").strip()
        row["media_base_url"] = str(row.get("media_base_url") or "").strip()
        return row

    def save_settings(self, data: dict[str, Any]) -> dict[str, Any]:
        from navin.marketing.media_delivery import validate_media_base_url

        current = self.load_settings()
        merged = {**current, **data}
        mode = str(merged.get("execution_mode") or "approval").strip().lower()
        if mode not in {"autonomous", "approval"}:
            raise MarketingError("execution_mode must be autonomous or approval")
        merged["execution_mode"] = mode
        merged["auto_publish"] = bool(merged.get("auto_publish"))
        merged["ai_assist"] = merged.get("ai_assist") is not False
        try:
            merged["winner_multiple"] = max(1.1, float(merged.get("winner_multiple") or 2.0))
        except (TypeError, ValueError) as exc:
            raise MarketingError("winner_multiple must be a number") from exc
        # Nested connector blocks merge channel by channel so one form never wipes another.
        if isinstance(data.get("publish"), dict):
            publish = dict(current.get("publish") or {})
            for key, value in data["publish"].items():
                if isinstance(value, dict) and isinstance(publish.get(key), dict):
                    publish[key] = {**publish[key], **value}
                else:
                    publish[key] = value
            merged["publish"] = publish
        merged["publish"] = normalize_publish(merged.get("publish"))
        if isinstance(data.get("analytics"), dict):
            merged["analytics"] = {**(current.get("analytics") or {}), **data["analytics"]}
        merged["analytics"] = normalize_analytics_source(merged.get("analytics"))
        if isinstance(data.get("channels"), dict):
            merged["channels"] = {**(current.get("channels") or {}), **data["channels"]}
        merged["channels"] = normalize_alert_channels(merged.get("channels"))
        merged["utm_campaign"] = str(merged.get("utm_campaign") or "").strip()
        merged["media_base_url"] = validate_media_base_url(str(merged.get("media_base_url") or ""))
        return self._save_object("settings.json", merged)

    # -- secrets (API keys) -------------------------------------------------

    def secrets_path(self) -> Path:
        return self.root / "secrets.json"

    def load_secrets(self) -> dict[str, str]:
        raw = _read_json(self.secrets_path(), {})
        if not isinstance(raw, dict):
            return {}
        return {str(key): str(value) for key, value in raw.items() if isinstance(value, str) and value}

    def save_secret(self, name: str, value: str) -> None:
        """Store one key at 0600. An empty value removes the key."""
        self.save_secrets({name: value})

    def save_secrets(self, values: dict[str, str]) -> None:
        """Rotate a token pair atomically without losing other connectors' keys."""
        from navin.utils.atomic_io import InterProcessLock

        known = {key for pair in SECRET_NAMES.values() for key in pair}
        unknown = set(values) - known
        if unknown:
            raise MarketingError(f"unknown secret {sorted(unknown)[0]}", status=400)
        with InterProcessLock(self.root / ".secrets.lock", timeout=5):
            secrets = self.load_secrets()
            for name, value in values.items():
                if value:
                    secrets[name] = value.strip()
                else:
                    secrets.pop(name, None)
            path = self.secrets_path()
            _atomic_write(path, secrets)
            try:
                os.chmod(path, 0o600)
            except OSError:
                pass

    def get_secret(self, name: str) -> str:
        return self.load_secrets().get(name, "")

    def has_secrets(self, channel: str) -> bool:
        wanted = SECRET_NAMES.get(channel, ())
        secrets = self.load_secrets()
        return all(secrets.get(key) for key in wanted)

    def load_campaigns(self) -> list[dict[str, Any]]:
        return self._load_rows("campaigns.json")

    def save_campaigns(self, rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
        return self._save_rows("campaigns.json", rows)

    def upsert_campaign(self, row: dict[str, Any]) -> dict[str, Any]:
        rows = self.load_campaigns()
        cid = str(row.get("id") or _new_id("cmp"))
        existing = next((item for item in rows if item.get("id") == cid), None)
        status = str(row.get("status") or (existing or {}).get("status") or "draft")
        if status not in CAMPAIGN_STATUSES:
            raise MarketingError(f"unknown campaign status {status}")
        channels = _split(row.get("channels") or (existing or {}).get("channels") or list(CHANNELS[:4]))
        merged = {
            **(existing or {}),
            **row,
            "id": cid,
            "status": status,
            "channels": channels,
            "updated_at": _now(),
        }
        if existing is None:
            merged.setdefault("created_at", _now())
            rows.insert(0, merged)
        else:
            rows = [merged if item.get("id") == cid else item for item in rows]
        self.save_campaigns(rows)
        return merged

    def get_campaign(self, cid: str) -> dict[str, Any]:
        for row in self.load_campaigns():
            if row.get("id") == cid:
                return row
        raise MarketingError(f"campaign {cid} not found", status=404)

    def load_content(self) -> list[dict[str, Any]]:
        return self._load_rows("content.json")

    def save_content(self, rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
        return self._save_rows("content.json", rows[:400])

    def upsert_content(self, row: dict[str, Any]) -> dict[str, Any]:
        rows = self.load_content()
        cid = str(row.get("id") or _new_id("cnt"))
        existing = next((item for item in rows if item.get("id") == cid), None)
        status = str(row.get("status") or (existing or {}).get("status") or "draft")
        if status not in CONTENT_STATUSES:
            raise MarketingError(f"unknown content status {status}")
        channel = str(row.get("channel") or (existing or {}).get("channel") or "linkedin").lower()
        if channel not in CHANNELS:
            raise MarketingError(f"unknown channel {channel}")
        merged = {
            **(existing or {}),
            **row,
            "id": cid,
            "status": status,
            "channel": channel,
            "updated_at": _now(),
        }
        if existing is None:
            merged.setdefault("created_at", _now())
            rows.insert(0, merged)
        else:
            rows = [merged if item.get("id") == cid else item for item in rows]
        self.save_content(rows)
        return merged

    def get_content(self, cid: str) -> dict[str, Any]:
        for row in self.load_content():
            if row.get("id") == cid:
                return row
        raise MarketingError(f"content {cid} not found", status=404)

    def load_creatives(self) -> list[dict[str, Any]]:
        return self._load_rows("creatives.json")

    def save_creatives(self, rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
        return self._save_rows("creatives.json", rows[:200])

    def upsert_creative(self, row: dict[str, Any]) -> dict[str, Any]:
        rows = self.load_creatives()
        cid = str(row.get("id") or _new_id("crv"))
        existing = next((item for item in rows if item.get("id") == cid), None)
        kind = str(row.get("kind") or (existing or {}).get("kind") or "image").lower()
        if kind not in CREATIVE_KINDS:
            raise MarketingError(f"unknown creative kind {kind}")
        merged = {
            **(existing or {}),
            **row,
            "id": cid,
            "kind": kind,
            "updated_at": _now(),
        }
        if existing is None:
            merged.setdefault("created_at", _now())
            merged.setdefault("status", "brief")
            rows.insert(0, merged)
        else:
            rows = [merged if item.get("id") == cid else item for item in rows]
        self.save_creatives(rows)
        return merged

    def load_experiments(self) -> list[dict[str, Any]]:
        return self._load_rows("experiments.json")

    def save_experiments(self, rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
        return self._save_rows("experiments.json", rows[:100])

    def upsert_experiment(self, row: dict[str, Any]) -> dict[str, Any]:
        rows = self.load_experiments()
        eid = str(row.get("id") or _new_id("exp"))
        existing = next((item for item in rows if item.get("id") == eid), None)
        merged = {**(existing or {}), **row, "id": eid, "updated_at": _now()}
        if existing is None:
            merged.setdefault("created_at", _now())
            rows.insert(0, merged)
        else:
            rows = [merged if item.get("id") == eid else item for item in rows]
        self.save_experiments(rows)
        return merged

    def load_competitors(self) -> list[dict[str, Any]]:
        return self._load_rows("competitors.json")

    def save_competitors(self, rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
        return self._save_rows("competitors.json", rows[:80])

    def upsert_competitor(self, row: dict[str, Any]) -> dict[str, Any]:
        rows = self.load_competitors()
        name = str(row.get("name") or "").strip()
        if not name:
            raise MarketingError("competitor name is required")
        cid = str(row.get("id") or "")
        existing = next(
            (
                item
                for item in rows
                if (cid and item.get("id") == cid)
                or str(item.get("name") or "").strip().lower() == name.lower()
            ),
            None,
        )
        merged = {
            **(existing or {}),
            **row,
            "id": str((existing or {}).get("id") or cid or _new_id("cmptr")),
            "name": name,
            "updated_at": _now(),
        }
        if existing is None:
            merged.setdefault("created_at", _now())
            rows.insert(0, merged)
        else:
            rows = [merged if item.get("id") == merged["id"] else item for item in rows]
        self.save_competitors(rows)
        return merged

    def load_launch(self) -> dict[str, Any]:
        raw = self._load_object(
            "launch.json",
            {"schema": SCHEMA, "status": "idle", "items": [], "updated_at": 0.0},
        )
        if not isinstance(raw.get("items"), list):
            raw["items"] = []
        return raw

    def save_launch(self, data: dict[str, Any]) -> dict[str, Any]:
        current = self.load_launch()
        merged = {**current, **data}
        if not isinstance(merged.get("items"), list):
            merged["items"] = current.get("items") or []
        return self._save_object("launch.json", merged)

    def load_loop(self) -> dict[str, Any]:
        raw = _read_json(self.path("loop.json"), {})
        if not isinstance(raw, dict) or not raw:
            raw = {
                "enabled": False,
                "phase": "idle",
                "next_due": 0.0,
                "last_tick": 0.0,
                "last_watch": 0.0,
                "last_result": "loop is paused - start it from the Marketing desk",
                "cycle": 0,
                "skipped_reason": "",
                "last_fingerprint": "",
                "schedule": {
                    "kind": "daily",
                    "hour": 9,
                    "minute": 0,
                    "weekday": 1,
                    "day": 1,
                },
            }
            self.save_loop(raw)
            return raw
        healed = heal_loop_state(raw)
        if _loop_needs_persist(raw, healed):
            return self.save_loop(healed)
        return healed

    def save_loop(self, data: dict[str, Any]) -> dict[str, Any]:
        row = dict(data)
        row["updated_at"] = _now()
        _atomic_write(self.path("loop.json"), row)
        return row

    def loop_intent_path(self) -> Path:
        return self.path("loop.intent.json")

    def load_loop_intent(self) -> dict[str, Any]:
        """Start/stop/schedule written while a cycle holds the desk lock."""
        raw = _read_json(self.loop_intent_path(), {})
        return raw if isinstance(raw, dict) else {}

    def save_loop_intent(self, data: dict[str, Any]) -> dict[str, Any]:
        payload = {key: value for key, value in dict(data).items() if key != "updated_at"}
        payload["updated_at"] = _now()
        _atomic_write(self.loop_intent_path(), payload)
        return payload

    def clear_loop_intent(self) -> None:
        try:
            self.loop_intent_path().unlink()
        except FileNotFoundError:
            return

    def load_journal(self, limit: int = 80) -> list[dict[str, Any]]:
        rows = self._load_rows("journal.json")
        return rows[: max(1, min(limit, 200))]

    def append_journal(self, row: dict[str, Any]) -> dict[str, Any]:
        rows = self._load_rows("journal.json")
        item = {
            **row,
            "id": str(row.get("id") or _new_id("j")),
            "t": float(row.get("t") or _now()),
        }
        rows.insert(0, item)
        self._save_rows("journal.json", rows[:200])
        return item
