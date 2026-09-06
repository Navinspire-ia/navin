"""Marketplace skill package signing (HMAC-SHA256, mirrors site/src/lib/marketplace.ts)."""

from __future__ import annotations

import base64
import hashlib
import hmac
import os
from typing import Mapping

MIN_MARKETPLACE_SECRET_LENGTH = 32
_CANONICAL_PREFIX = "navin-marketplace-v1"


class MarketplaceSigningError(ValueError):
    """Raised when signing cannot proceed (missing secret or inputs)."""


def marketplace_signing_secret() -> str:
    return (os.environ.get("MARKETPLACE_SIGNING_SECRET") or "").strip()


def is_marketplace_signing_configured(secret: str | None = None) -> bool:
    value = marketplace_signing_secret() if secret is None else secret
    return len(value) >= MIN_MARKETPLACE_SECRET_LENGTH


def canonical_skill_payload(
    *,
    slug: str,
    version: str,
    content_hash: str | None = None,
    package_url: str | None = None,
) -> str:
    return "\n".join(
        [
            _CANONICAL_PREFIX,
            (slug or "").strip(),
            (version or "").strip(),
            (content_hash or "").strip(),
            (package_url or "").strip(),
        ]
    )


def _b64url(raw: bytes) -> str:
    return base64.urlsafe_b64encode(raw).decode("ascii").rstrip("=")


def sign_skill_package(
    *,
    slug: str,
    version: str,
    content_hash: str | None = None,
    package_url: str | None = None,
    secret: str | None = None,
) -> str:
    """Sign a skill package. Requires slug, version, and content_hash or package_url."""
    key = marketplace_signing_secret() if secret is None else secret
    if not is_marketplace_signing_configured(key):
        raise MarketplaceSigningError(
            f"MARKETPLACE_SIGNING_SECRET missing or too short "
            f"({MIN_MARKETPLACE_SECRET_LENGTH} characters minimum)"
        )
    slug_n = (slug or "").strip()
    version_n = (version or "").strip()
    content_hash_n = (content_hash or "").strip()
    package_url_n = (package_url or "").strip()
    if not slug_n or not version_n:
        raise MarketplaceSigningError("slug and version are required")
    if not content_hash_n and not package_url_n:
        raise MarketplaceSigningError("content_hash or package_url is required")
    payload = canonical_skill_payload(
        slug=slug_n,
        version=version_n,
        content_hash=content_hash_n,
        package_url=package_url_n,
    )
    digest = hmac.new(key.encode("utf-8"), payload.encode("utf-8"), hashlib.sha256).digest()
    return _b64url(digest)


def verify_skill_signature(
    *,
    slug: str,
    version: str,
    signature: str | None,
    content_hash: str | None = None,
    package_url: str | None = None,
    secret: str | None = None,
) -> bool:
    """Verify a marketplace signature. Rejects unsigned / empty signatures."""
    sig = (signature or "").strip()
    if not sig:
        return False
    key = marketplace_signing_secret() if secret is None else secret
    if not is_marketplace_signing_configured(key):
        return False
    try:
        expected = sign_skill_package(
            slug=slug,
            version=version,
            content_hash=content_hash,
            package_url=package_url,
            secret=key,
        )
    except MarketplaceSigningError:
        return False
    return hmac.compare_digest(sig, expected)


def assert_featured_eligible(fields: Mapping[str, str | None]) -> None:
    """Featured skills must carry a valid signature (skill-vetter + sign)."""
    ok = verify_skill_signature(
        slug=str(fields.get("slug") or ""),
        version=str(fields.get("version") or ""),
        signature=fields.get("signature"),
        content_hash=fields.get("content_hash"),
        package_url=fields.get("package_url"),
    )
    if not ok:
        raise MarketplaceSigningError(
            "unsigned or invalid skill signature: run skill-vetter then sign before featuring"
        )
