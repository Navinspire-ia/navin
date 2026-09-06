"""PageSpeed Insights v5 and CrUX v1 clients."""

from __future__ import annotations

import os
from typing import Any

import httpx

PSI_ENDPOINT = "https://www.googleapis.com/pagespeedonline/v5/runPagespeed"
CRUX_ENDPOINT = "https://chromeuxreport.googleapis.com/v1/records:queryRecord"


def _gap(service: str, env_name: str) -> dict[str, Any]:
    return {
        "status": "data_gap",
        "service": service,
        "reason": f"Missing optional credential: {env_name}",
        "metrics": None,
    }


class PerformanceClient:
    def __init__(
        self,
        *,
        psi_api_key: str | None = None,
        crux_api_key: str | None = None,
        timeout: float = 30,
        transport: httpx.BaseTransport | None = None,
    ) -> None:
        self.psi_api_key = psi_api_key or os.getenv("PAGESPEED_API_KEY")
        self.crux_api_key = crux_api_key or os.getenv("CRUX_API_KEY") or self.psi_api_key
        self.timeout = timeout
        self.transport = transport

    def psi(self, url: str, *, strategy: str = "mobile") -> dict[str, Any]:
        if not self.psi_api_key:
            return _gap("psi_v5", "PAGESPEED_API_KEY")
        try:
            with httpx.Client(timeout=self.timeout, transport=self.transport) as client:
                response = client.get(
                    PSI_ENDPOINT,
                    params={
                        "url": url,
                        "strategy": strategy,
                        "category": "performance",
                        "key": self.psi_api_key,
                    },
                )
                response.raise_for_status()
                payload = response.json()
        except (httpx.HTTPError, ValueError) as exc:
            status = exc.response.status_code if isinstance(exc, httpx.HTTPStatusError) else "network"
            raise RuntimeError(f"PSI v5 request failed ({status})") from None
        lighthouse = payload.get("lighthouseResult") or {}
        categories = lighthouse.get("categories") or {}
        audits = lighthouse.get("audits") or {}
        return {
            "status": "ok",
            "service": "psi_v5",
            "source": PSI_ENDPOINT,
            "url": url,
            "strategy": strategy,
            "performance_score": (categories.get("performance") or {}).get("score"),
            "metrics": {
                name: (audits.get(audit) or {}).get("numericValue")
                for name, audit in {
                    "lcp_ms": "largest-contentful-paint",
                    "cls": "cumulative-layout-shift",
                    "tbt_ms": "total-blocking-time",
                    "speed_index_ms": "speed-index",
                }.items()
            },
            "raw": payload,
        }

    def crux(self, *, url: str | None = None, origin: str | None = None) -> dict[str, Any]:
        if not self.crux_api_key:
            return _gap("crux_v1", "CRUX_API_KEY or PAGESPEED_API_KEY")
        if bool(url) == bool(origin):
            raise ValueError("Provide exactly one of url or origin")
        body = {"url": url} if url else {"origin": origin}
        try:
            with httpx.Client(timeout=self.timeout, transport=self.transport) as client:
                response = client.post(
                    CRUX_ENDPOINT,
                    params={"key": self.crux_api_key},
                    json=body,
                )
                response.raise_for_status()
                payload = response.json()
        except (httpx.HTTPError, ValueError) as exc:
            status = exc.response.status_code if isinstance(exc, httpx.HTTPStatusError) else "network"
            raise RuntimeError(f"CrUX v1 request failed ({status})") from None
        return {
            "status": "ok",
            "service": "crux_v1",
            "source": CRUX_ENDPOINT,
            "target": url or origin,
            "metrics": (payload.get("record") or {}).get("metrics") or {},
            "raw": payload,
        }
