# Copyright (c) 2026-present Navinspire IA
# SPDX-License-Identifier: AGPL-3.0-only

"""TLS trust for endpoints behind an internal PKI."""

from __future__ import annotations

import ssl

import certifi


def trust_with_extra_ca(ca_bundle: str | None) -> ssl.SSLContext | bool:
    """``verify`` value for httpx: the public roots, plus *ca_bundle* when given.

    A company gateway signed by the company CA fails verification against the
    public roots alone, and replacing them would break every public endpoint.
    Raises ValueError naming the file when it cannot be loaded.
    """
    if not ca_bundle:
        return True
    context = ssl.create_default_context(cafile=certifi.where())
    try:
        context.load_verify_locations(cafile=ca_bundle)
    except (OSError, ssl.SSLError) as exc:
        raise ValueError(f"CA bundle {ca_bundle} cannot be loaded: {exc}") from exc
    return context
