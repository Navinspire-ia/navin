"""Download a Figma frame as a PNG reference through the official REST API."""

from __future__ import annotations

import os
import re
from collections.abc import Callable
from pathlib import Path
from typing import Any
from urllib.parse import parse_qs, unquote, urlparse

import httpx

FIGMA_API = "https://api.figma.com/v1"


class FigmaError(RuntimeError):
    """Raised for any Figma fetch failure (token, node, HTTP)."""


def parse_figma_url(value: str, node_id: str | None = None) -> tuple[str, str | None]:
    """Return ``(file_key, node_id)`` from a Figma URL or a bare file key.

    URL node ids use dashes (``1-2``); the API expects colons (``1:2``).
    """
    text = (value or "").strip()
    if not text:
        raise FigmaError("Figma file key or URL is required")
    node = node_id
    if "figma.com" in text:
        parsed = urlparse(text if "://" in text else f"https://{text}")
        match = re.match(r"^/(?:file|design|proto)/([A-Za-z0-9]+)", parsed.path)
        if not match:
            raise FigmaError(f"Unrecognized Figma URL: {text}")
        key = match.group(1)
        if node is None:
            values = parse_qs(parsed.query).get("node-id")
            node = unquote(values[0]) if values else None
    else:
        if not re.fullmatch(r"[A-Za-z0-9]+", text):
            raise FigmaError(f"Invalid Figma file key: {text}")
        key = text
    if node:
        node = node.replace("-", ":")
    return key, node


def fetch_figma_frame(
    file_key: str,
    node_id: str | None,
    out_path: str | Path,
    token: str | None = None,
    http: Callable[..., Any] | None = None,
) -> dict[str, Any]:
    """Render one Figma node to PNG and save it to ``out_path``.

    ``http(method, url, headers=..., params=...)`` must return an object with
    ``status_code``, ``json()`` and ``content`` (an ``httpx.Response`` fits).
    """
    key, node = parse_figma_url(file_key, node_id)
    if not node:
        raise FigmaError("Figma node id is required (node_id or ?node-id= in the URL)")
    tok = token or os.environ.get("FIGMA_TOKEN", "")
    if not tok:
        raise FigmaError("Figma token missing: pass token or set FIGMA_TOKEN")
    call = http or _default_http
    url = f"{FIGMA_API}/images/{key}"
    resp = call(
        "GET", url, headers={"X-Figma-Token": tok}, params={"ids": node, "format": "png"}
    )
    if resp.status_code != 200:
        raise FigmaError(f"Figma API HTTP {resp.status_code} for {url}")
    data = resp.json()
    if data.get("err"):
        raise FigmaError(f"Figma API error: {data['err']}")
    image_url = (data.get("images") or {}).get(node)
    if not image_url:
        raise FigmaError(f"Figma node not found or not renderable: {node}")
    img = call("GET", image_url, headers={}, params=None)
    if img.status_code != 200:
        raise FigmaError(f"Figma image download HTTP {img.status_code}")
    out = Path(out_path)
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_bytes(img.content)
    return {"file_key": key, "node_id": node, "path": str(out), "bytes": len(img.content)}


def _default_http(method: str, url: str, headers: dict, params: dict | None) -> httpx.Response:
    return httpx.request(method, url, headers=headers, params=params, timeout=60, follow_redirects=True)
