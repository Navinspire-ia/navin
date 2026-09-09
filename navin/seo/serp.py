# Copyright (c) 2026-present Navinspire IA
# SPDX-License-Identifier: AGPL-3.0-only

"""SERP providers and an atomic append-only snapshot store."""

from __future__ import annotations

import csv
import io
import json
import os
import tempfile
from datetime import datetime, timezone
from pathlib import Path
from typing import Protocol

import httpx
from filelock import FileLock
from pydantic import BaseModel, ConfigDict, Field

from navin.seo.models import Confidence


class SerpResult(BaseModel):
    model_config = ConfigDict(extra="forbid")

    position: int = Field(ge=1)
    url: str
    title: str = ""
    result_type: str = "organic"
    source: str
    confidence: Confidence


class SerpSnapshot(BaseModel):
    model_config = ConfigDict(extra="forbid")

    schema_version: str = "1.0"
    captured_at: str
    keyword: str
    location: str
    language: str
    source: str
    confidence: Confidence
    results: list[SerpResult]


class SerpAdapter(Protocol):
    source: str

    def snapshot(self, keyword: str, *, location: str, language: str) -> SerpSnapshot: ...


def _now() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat()


class DataForSeoAdapter:
    source = "dataforseo"
    endpoint = "https://api.dataforseo.com/v3/serp/google/organic/live/advanced"

    def __init__(
        self, login: str, password: str, *, transport: httpx.BaseTransport | None = None
    ) -> None:
        if not login or not password:
            raise ValueError("DataForSEO credentials are required")
        self.login = login
        self.password = password
        self.transport = transport

    def snapshot(self, keyword: str, *, location: str, language: str) -> SerpSnapshot:
        task = {"keyword": keyword, "location_name": location, "language_code": language, "depth": 100}
        try:
            with httpx.Client(transport=self.transport, timeout=30) as client:
                response = client.post(self.endpoint, auth=(self.login, self.password), json=[task])
                response.raise_for_status()
                payload = response.json()
        except (httpx.HTTPError, ValueError) as exc:
            status = exc.response.status_code if isinstance(exc, httpx.HTTPStatusError) else "network"
            raise RuntimeError(f"DataForSEO request failed ({status})") from None
        tasks = payload.get("tasks") or []
        items = (((tasks[0].get("result") or [{}])[0]).get("items") or []) if tasks else []
        results = [
            SerpResult(
                position=int(item["rank_absolute"]),
                url=str(item["url"]),
                title=str(item.get("title") or ""),
                result_type=str(item.get("type") or "organic"),
                source=self.source,
                confidence=Confidence.HIGH,
            )
            for item in items
            if item.get("rank_absolute") and item.get("url")
        ]
        return SerpSnapshot(
            captured_at=_now(), keyword=keyword, location=location, language=language,
            source=self.source, confidence=Confidence.HIGH, results=results,
        )


class SemrushAdapter:
    source = "semrush"
    endpoint = "https://api.semrush.com/"

    def __init__(self, api_key: str, *, transport: httpx.BaseTransport | None = None) -> None:
        if not api_key:
            raise ValueError("Semrush API key is required")
        self.api_key = api_key
        self.transport = transport

    def snapshot(self, keyword: str, *, location: str, language: str) -> SerpSnapshot:
        try:
            with httpx.Client(transport=self.transport, timeout=30) as client:
                response = client.get(self.endpoint, params={
                    "type": "phrase_organic", "key": self.api_key, "phrase": keyword,
                    "database": location, "display_limit": 100,
                    "export_columns": "Dn,Ur,Po,Tt",
                })
                response.raise_for_status()
        except httpx.HTTPError as exc:
            status = exc.response.status_code if isinstance(exc, httpx.HTTPStatusError) else "network"
            raise RuntimeError(f"Semrush request failed ({status})") from None
        rows = list(csv.DictReader(io.StringIO(response.text), delimiter=";"))
        results: list[SerpResult] = []
        for row in rows:
            position = row.get("Position") or row.get("Po")
            url = row.get("Url") or row.get("Ur")
            if not position or not str(position).isdigit() or not url:
                continue
            results.append(SerpResult(
                position=int(position), url=url,
                title=row.get("Title") or row.get("Tt") or "",
                source=self.source, confidence=Confidence.HIGH,
            ))
        return SerpSnapshot(
            captured_at=_now(), keyword=keyword, location=location, language=language,
            source=self.source, confidence=Confidence.HIGH, results=results,
        )


class SerpStore:
    """JSONL event store rewritten atomically while preserving all prior events."""

    def __init__(self, path: str | Path) -> None:
        self.path = Path(path)
        self.lock = FileLock(str(self.path) + ".lock")

    def append(self, snapshot: SerpSnapshot) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        encoded = json.dumps(
            snapshot.model_dump(mode="json"), ensure_ascii=False, sort_keys=True,
            separators=(",", ":"),
        ) + "\n"
        with self.lock:
            existing = self.path.read_bytes() if self.path.exists() else b""
            fd, temp_name = tempfile.mkstemp(prefix=self.path.name + ".", dir=self.path.parent)
            try:
                with os.fdopen(fd, "wb") as handle:
                    handle.write(existing)
                    handle.write(encoded.encode("utf-8"))
                    handle.flush()
                    os.fsync(handle.fileno())
                os.replace(temp_name, self.path)
            finally:
                if os.path.exists(temp_name):
                    os.unlink(temp_name)

    def history(self, *, keyword: str | None = None) -> list[SerpSnapshot]:
        if not self.path.exists():
            return []
        snapshots = [
            SerpSnapshot.model_validate_json(line)
            for line in self.path.read_text(encoding="utf-8").splitlines()
            if line.strip()
        ]
        if keyword is not None:
            snapshots = [item for item in snapshots if item.keyword == keyword]
        return snapshots
