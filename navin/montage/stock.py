# Copyright (c) 2026-present Navinspire IA
# SPDX-License-Identifier: AGPL-3.0-only

"""Builtin stock media clients (Pexels / Unsplash / Pixabay).

No third-party SDKs - stdlib + httpx already used by Navin. Keys come from
config (``tools.montage.stock.*``) or environment variables. Never treat a
non-empty key as "ready" without a live probe when ``probe=True``.
"""

from __future__ import annotations

import os
from dataclasses import dataclass
from typing import Any

import httpx

_TIMEOUT_S = 12.0

STOCK_PROVIDERS = ("pexels", "unsplash", "pixabay")


@dataclass(slots=True)
class StockHit:
    provider: str
    kind: str  # image | video
    id: str
    url: str
    preview_url: str
    width: int | None = None
    height: int | None = None
    credit: str = ""
    page_url: str = ""

    def as_dict(self) -> dict[str, Any]:
        return {
            "provider": self.provider,
            "kind": self.kind,
            "id": self.id,
            "url": self.url,
            "preview_url": self.preview_url,
            "width": self.width,
            "height": self.height,
            "credit": self.credit,
            "page_url": self.page_url,
        }


def _env(name: str) -> str:
    return (os.environ.get(name) or "").strip()


def resolve_stock_keys(config: Any | None = None) -> dict[str, str]:
    """Merge config.tools.montage.stock keys with environment fallbacks."""
    stock_cfg = None
    if config is not None:
        tools = getattr(config, "tools", None)
        montage = getattr(tools, "montage", None) if tools is not None else None
        stock_cfg = getattr(montage, "stock", None) if montage is not None else None

    def pick(attr: str, *env_names: str) -> str:
        value = ""
        if stock_cfg is not None:
            value = str(getattr(stock_cfg, attr, "") or "").strip()
        if value:
            return value
        for env_name in env_names:
            value = _env(env_name)
            if value:
                return value
        return ""

    return {
        "pexels": pick("pexels_api_key", "PEXELS_API_KEY"),
        "unsplash": pick("unsplash_access_key", "UNSPLASH_ACCESS_KEY", "UNSPLASH_API_KEY"),
        "pixabay": pick("pixabay_api_key", "PIXABAY_API_KEY"),
    }


def stock_status(config: Any | None = None, *, probe: bool = False) -> dict[str, Any]:
    """Readiness for each builtin stock provider.

    Without ``probe``, reports whether a key is configured. With ``probe``,
    runs a cheap authenticated request so a stale/commented key cannot look
    ready (OpenMontage #431).
    """
    keys = resolve_stock_keys(config)
    providers: dict[str, Any] = {}
    for name in STOCK_PROVIDERS:
        key = keys.get(name) or ""
        row: dict[str, Any] = {
            "provider": name,
            "configured": bool(key),
            "ready": bool(key) and not probe,
            "signup": {
                "pexels": "https://www.pexels.com/api/",
                "unsplash": "https://unsplash.com/oauth/applications",
                "pixabay": "https://pixabay.com/api/docs/",
            }[name],
        }
        if probe and key:
            ok, detail = _probe_provider(name, key)
            row["ready"] = ok
            row["detail"] = detail
        elif not key:
            row["detail"] = "API key missing"
            row["ready"] = False
        else:
            row["detail"] = "key configured (not probed)"
        providers[name] = row
    return {
        "tier": "builtin",
        "ready_any": any(p["ready"] for p in providers.values()),
        "providers": providers,
    }


def _probe_provider(name: str, key: str) -> tuple[bool, str]:
    try:
        if name == "pexels":
            r = httpx.get(
                "https://api.pexels.com/v1/search",
                params={"query": "office", "per_page": 1},
                headers={"Authorization": key},
                timeout=_TIMEOUT_S,
            )
        elif name == "unsplash":
            r = httpx.get(
                "https://api.unsplash.com/search/photos",
                params={"query": "office", "per_page": 1},
                headers={"Authorization": f"Client-ID {key}", "Accept-Version": "v1"},
                timeout=_TIMEOUT_S,
            )
        else:
            r = httpx.get(
                "https://pixabay.com/api/",
                params={"key": key, "q": "office", "per_page": 3},
                timeout=_TIMEOUT_S,
            )
    except httpx.HTTPError as exc:
        return False, f"network error: {exc}"
    if r.status_code == 200:
        return True, "live probe ok"
    if r.status_code in {401, 403}:
        return False, f"auth failed ({r.status_code})"
    return False, f"HTTP {r.status_code}"


def search_stock(
    provider: str,
    query: str,
    *,
    kind: str = "image",
    per_page: int = 8,
    config: Any | None = None,
) -> dict[str, Any]:
    """Search a stock provider. Returns structured hits (no disk writes)."""
    name = (provider or "").strip().lower()
    if name not in STOCK_PROVIDERS:
        return {
            "ok": False,
            "error": f"unknown provider {provider!r}",
            "fix": f"Use one of: {', '.join(STOCK_PROVIDERS)}",
        }
    q = (query or "").strip()
    if not q:
        return {"ok": False, "error": "query required", "fix": "Pass query=..."}
    keys = resolve_stock_keys(config)
    key = keys.get(name) or ""
    if not key:
        return {
            "ok": False,
            "error": "not_configured",
            "fix": (
                f"Set {name} API key in Settings → Montage stock or env "
                f"({', '.join({'pexels': ('PEXELS_API_KEY',), 'unsplash': ('UNSPLASH_ACCESS_KEY',), 'pixabay': ('PIXABAY_API_KEY',)}[name])})."
            ),
        }
    media_kind = "video" if (kind or "").lower().startswith("v") else "image"
    try:
        if name == "pexels":
            hits = _search_pexels(key, q, media_kind, per_page)
        elif name == "unsplash":
            if media_kind == "video":
                return {
                    "ok": False,
                    "error": "unsupported",
                    "fix": "Unsplash is photos-only; use Pexels or Pixabay for video.",
                }
            hits = _search_unsplash(key, q, per_page)
        else:
            hits = _search_pixabay(key, q, media_kind, per_page)
    except httpx.HTTPError as exc:
        return {"ok": False, "error": "network", "fix": str(exc)}
    return {
        "ok": True,
        "provider": name,
        "query": q,
        "kind": media_kind,
        "count": len(hits),
        "hits": [h.as_dict() for h in hits],
    }


def _search_pexels(key: str, query: str, kind: str, per_page: int) -> list[StockHit]:
    if kind == "video":
        url = "https://api.pexels.com/videos/search"
    else:
        url = "https://api.pexels.com/v1/search"
    r = httpx.get(
        url,
        params={"query": query, "per_page": max(1, min(per_page, 40))},
        headers={"Authorization": key},
        timeout=_TIMEOUT_S,
    )
    r.raise_for_status()
    data = r.json()
    hits: list[StockHit] = []
    if kind == "video":
        for item in data.get("videos") or []:
            files = item.get("video_files") or []
            best = max(files, key=lambda f: int(f.get("width") or 0), default=None)
            if not best:
                continue
            hits.append(
                StockHit(
                    provider="pexels",
                    kind="video",
                    id=str(item.get("id") or ""),
                    url=str(best.get("link") or ""),
                    preview_url=str((item.get("image") or "")),
                    width=best.get("width"),
                    height=best.get("height"),
                    credit=str((item.get("user") or {}).get("name") or "Pexels"),
                    page_url=str(item.get("url") or ""),
                )
            )
        return hits
    for item in data.get("photos") or []:
        src = item.get("src") or {}
        hits.append(
            StockHit(
                provider="pexels",
                kind="image",
                id=str(item.get("id") or ""),
                url=str(src.get("original") or src.get("large2x") or src.get("large") or ""),
                preview_url=str(src.get("medium") or src.get("small") or ""),
                width=item.get("width"),
                height=item.get("height"),
                credit=str(item.get("photographer") or "Pexels"),
                page_url=str(item.get("url") or ""),
            )
        )
    return hits


def _search_unsplash(key: str, query: str, per_page: int) -> list[StockHit]:
    r = httpx.get(
        "https://api.unsplash.com/search/photos",
        params={"query": query, "per_page": max(1, min(per_page, 30))},
        headers={"Authorization": f"Client-ID {key}", "Accept-Version": "v1"},
        timeout=_TIMEOUT_S,
    )
    r.raise_for_status()
    hits: list[StockHit] = []
    for item in (r.json().get("results") or []):
        urls = item.get("urls") or {}
        user = item.get("user") or {}
        hits.append(
            StockHit(
                provider="unsplash",
                kind="image",
                id=str(item.get("id") or ""),
                url=str(urls.get("full") or urls.get("raw") or urls.get("regular") or ""),
                preview_url=str(urls.get("small") or urls.get("thumb") or ""),
                width=item.get("width"),
                height=item.get("height"),
                credit=str(user.get("name") or "Unsplash"),
                page_url=str((item.get("links") or {}).get("html") or ""),
            )
        )
    return hits


def _search_pixabay(key: str, query: str, kind: str, per_page: int) -> list[StockHit]:
    if kind == "video":
        endpoint = "https://pixabay.com/api/videos/"
    else:
        endpoint = "https://pixabay.com/api/"
    # Pixabay rejects per_page < 3.
    r = httpx.get(
        endpoint,
        params={
            "key": key,
            "q": query,
            "per_page": max(3, min(per_page, 50)),
        },
        timeout=_TIMEOUT_S,
    )
    r.raise_for_status()
    hits: list[StockHit] = []
    if kind == "video":
        for item in r.json().get("hits") or []:
            videos = item.get("videos") or {}
            best = videos.get("large") or videos.get("medium") or videos.get("small") or {}
            hits.append(
                StockHit(
                    provider="pixabay",
                    kind="video",
                    id=str(item.get("id") or ""),
                    url=str(best.get("url") or ""),
                    preview_url=str(item.get("userImageURL") or ""),
                    width=best.get("width"),
                    height=best.get("height"),
                    credit=str(item.get("user") or "Pixabay"),
                    page_url=str(item.get("pageURL") or ""),
                )
            )
        return hits
    for item in r.json().get("hits") or []:
        hits.append(
            StockHit(
                provider="pixabay",
                kind="image",
                id=str(item.get("id") or ""),
                url=str(item.get("largeImageURL") or item.get("webformatURL") or ""),
                preview_url=str(item.get("previewURL") or item.get("webformatURL") or ""),
                width=item.get("imageWidth"),
                height=item.get("imageHeight"),
                credit=str(item.get("user") or "Pixabay"),
                page_url=str(item.get("pageURL") or ""),
            )
        )
    return hits


def render_stock_status(status: dict[str, Any]) -> str:
    lines = ["Stock media (builtin, free developer keys):"]
    for name, row in (status.get("providers") or {}).items():
        mark = "ok" if row.get("ready") else ("key" if row.get("configured") else "no")
        lines.append(
            f"  [{mark}] {name}: {row.get('detail') or ('ready' if row.get('ready') else 'missing')}"
        )
        if not row.get("ready"):
            lines.append(f"       Signup: {row.get('signup')}")
    return "\n".join(lines)
