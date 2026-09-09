# Copyright (c) 2026-present Navinspire IA
# SPDX-License-Identifier: AGPL-3.0-only

"""Realistic browser identities for the scrape fetcher.

A single hard-coded ``NavinScrape/0.1`` User-Agent with no client hints is the
easiest signal for a bot filter to drop. These profiles present the request as
a mainstream desktop browser, with the ``Accept`` / ``Accept-Language`` /
``sec-ch-ua`` set that browser actually sends, so the header story is coherent
(a Chrome UA that ships Firefox client hints is itself a tell).

Selection is deterministic per host: one origin looks like one browser for the
whole crawl (no mid-session UA flip, which is suspicious), while different
origins vary. This is identity presentation, not challenge solving - captcha
and WAF handling still go through the evasion providers, opt-in and gated.
"""

from __future__ import annotations

import hashlib
from typing import Any
from urllib.parse import urlparse

# Each profile is a coherent (User-Agent, client-hint) bundle for one real
# browser build. Kept deliberately small and current; refresh as majors ship.
_PROFILES: tuple[dict[str, Any], ...] = (
    {
        "user_agent": (
            "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
            "(KHTML, like Gecko) Chrome/125.0.0.0 Safari/537.36"
        ),
        "headers": {
            "Accept": (
                "text/html,application/xhtml+xml,application/xml;q=0.9,"
                "image/avif,image/webp,image/apng,*/*;q=0.8"
            ),
            "Accept-Language": "en-US,en;q=0.9",
            "sec-ch-ua": '"Chromium";v="125", "Not.A/Brand";v="24", "Google Chrome";v="125"',
            "sec-ch-ua-mobile": "?0",
            "sec-ch-ua-platform": '"Windows"',
            "Upgrade-Insecure-Requests": "1",
        },
    },
    {
        "user_agent": (
            "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 "
            "(KHTML, like Gecko) Chrome/125.0.0.0 Safari/537.36"
        ),
        "headers": {
            "Accept": (
                "text/html,application/xhtml+xml,application/xml;q=0.9,"
                "image/avif,image/webp,image/apng,*/*;q=0.8"
            ),
            "Accept-Language": "en-US,en;q=0.9",
            "sec-ch-ua": '"Chromium";v="125", "Not.A/Brand";v="24", "Google Chrome";v="125"',
            "sec-ch-ua-mobile": "?0",
            "sec-ch-ua-platform": '"macOS"',
            "Upgrade-Insecure-Requests": "1",
        },
    },
    {
        "user_agent": (
            "Mozilla/5.0 (Windows NT 10.0; Win64; x64; rv:126.0) "
            "Gecko/20100101 Firefox/126.0"
        ),
        "headers": {
            "Accept": (
                "text/html,application/xhtml+xml,application/xml;q=0.9,"
                "image/avif,image/webp,*/*;q=0.8"
            ),
            "Accept-Language": "en-US,en;q=0.5",
            "Upgrade-Insecure-Requests": "1",
        },
    },
    {
        "user_agent": (
            "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/605.1.15 "
            "(KHTML, like Gecko) Version/17.4 Safari/605.1.15"
        ),
        "headers": {
            "Accept": (
                "text/html,application/xhtml+xml,application/xml;q=0.9,"
                "*/*;q=0.8"
            ),
            "Accept-Language": "en-US,en;q=0.9",
        },
    },
    {
        "user_agent": (
            "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
            "(KHTML, like Gecko) Chrome/125.0.0.0 Safari/537.36 Edg/125.0.0.0"
        ),
        "headers": {
            "Accept": (
                "text/html,application/xhtml+xml,application/xml;q=0.9,"
                "image/avif,image/webp,image/apng,*/*;q=0.8"
            ),
            "Accept-Language": "en-US,en;q=0.9",
            "sec-ch-ua": '"Microsoft Edge";v="125", "Chromium";v="125", "Not.A/Brand";v="24"',
            "sec-ch-ua-mobile": "?0",
            "sec-ch-ua-platform": '"Windows"',
            "Upgrade-Insecure-Requests": "1",
        },
    },
)

# The realistic default when rotation is off but the caller wants to look like
# a browser rather than the honest bot UA.
DEFAULT_BROWSER_UA = _PROFILES[0]["user_agent"]


def _host_of(url: str) -> str:
    try:
        return (urlparse(url).hostname or "").lower()
    except Exception:
        return ""


def profile_for_host(host: str) -> dict[str, Any]:
    """Pick a stable profile for *host* (same browser for the whole origin)."""
    if not host:
        return dict(_PROFILES[0])
    digest = hashlib.sha256(host.encode("utf-8")).digest()
    index = digest[0] % len(_PROFILES)
    chosen = _PROFILES[index]
    return {
        "user_agent": chosen["user_agent"],
        "headers": dict(chosen["headers"]),
    }


def profile_for_url(url: str) -> dict[str, Any]:
    return profile_for_host(_host_of(url))


def stealth_headers_for_urls(urls: list[str]) -> dict[str, str]:
    """Coherent browser headers for a batch.

    A batch usually targets one origin; we key on the first URL's host so the
    whole client presents one identity. Mixed-host batches still get a valid,
    realistic browser story (just tied to the first host).
    """
    first = next((u for u in urls if u), "")
    profile = profile_for_url(first)
    headers = dict(profile["headers"])
    headers["User-Agent"] = profile["user_agent"]
    return headers
