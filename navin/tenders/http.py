"""Shared HTTP helpers for official tender feeds."""

from __future__ import annotations

import json
import urllib.error
import urllib.request
from typing import Any

_UA = "NavinTenders/1.0 (+https://navin.ai)"
_TIMEOUT = 22


def _read_json(req: urllib.request.Request) -> Any:
    try:
        with urllib.request.urlopen(req, timeout=_TIMEOUT) as resp:  # noqa: S310
            return json.loads(resp.read().decode("utf-8", errors="replace"))
    except urllib.error.HTTPError as exc:
        detail = exc.read().decode("utf-8", errors="replace")[:240]
        raise ValueError(f"HTTP {exc.code}: {detail}") from exc


def get_json(url: str) -> Any:
    req = urllib.request.Request(url, headers={"User-Agent": _UA, "Accept": "application/json"})
    return _read_json(req)


def post_json(url: str, payload: dict[str, Any]) -> Any:
    body = json.dumps(payload).encode("utf-8")
    req = urllib.request.Request(
        url,
        data=body,
        method="POST",
        headers={
            "User-Agent": _UA,
            "Accept": "application/json",
            "Content-Type": "application/json",
        },
    )
    return _read_json(req)


def get_bytes(url: str, *, limit: int = 1_800_000) -> bytes:
    req = urllib.request.Request(url, headers={"User-Agent": _UA, "Accept": "text/csv,application/json,*/*"})
    try:
        with urllib.request.urlopen(req, timeout=_TIMEOUT) as resp:  # noqa: S310
            return resp.read(limit)
    except urllib.error.HTTPError as exc:
        detail = exc.read().decode("utf-8", errors="replace")[:240]
        raise ValueError(f"HTTP {exc.code}: {detail}") from exc
