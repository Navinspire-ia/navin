# Copyright (c) 2026-present Navinspire IA
# SPDX-License-Identifier: AGPL-3.0-only

"""Deterministic, dependency-free field normalizers for leads.

Kept stdlib-only on purpose: a leads pipeline runs across Windows / WSL / Linux /
macOS builds, so adding native phone/email libraries would be one more thing to
compile per platform. These normalizers are pragmatic (not a full RFC / libphone
implementation) but reproducible - the same input always yields the same output.
"""

from __future__ import annotations

import re
from urllib.parse import urlparse

# Deliberately conservative: matches the vast majority of real addresses without
# accepting obvious junk. A full RFC 5322 validator would accept forms no CRM
# will take anyway.
_EMAIL_RE = re.compile(r"^[A-Z0-9._%+\-]+@[A-Z0-9.\-]+\.[A-Z]{2,}$", re.I)
_URL_SCHEME_RE = re.compile(r"^[a-z][a-z0-9+.\-]*://", re.I)

# Common national trunk prefixes to drop when a country code is supplied.
_TRUNK_PREFIX = "0"

# Minimal country dialing hints (extend as needed). Maps ISO-2 -> dial code.
_DIAL_CODES = {
    "US": "1", "CA": "1", "GB": "44", "UK": "44", "FR": "33", "DE": "49",
    "ES": "34", "IT": "39", "NL": "31", "BE": "32", "CH": "41", "AT": "43",
    "IE": "353", "PT": "351", "SE": "46", "NO": "47", "DK": "45", "FI": "358",
    "AU": "61", "NZ": "64", "IN": "91", "SG": "65", "AE": "971", "MA": "212",
    "TN": "216", "DZ": "213", "BR": "55", "MX": "52", "JP": "81", "CN": "86",
}


def normalize_email(raw: str | None) -> str:
    """Lowercase, trim, and validate an email. Returns '' when clearly invalid."""
    value = (raw or "").strip().strip("<>").strip()
    if not value or " " in value:
        return ""
    # Strip a leading "mailto:".
    if value.lower().startswith("mailto:"):
        value = value[7:]
    value = value.lower()
    if not _EMAIL_RE.match(value):
        return ""
    return value


def normalize_domain(raw: str | None) -> str:
    """Reduce a website / URL / bare host to a lowercase registrable host."""
    value = (raw or "").strip().lower()
    if not value:
        return ""
    if not _URL_SCHEME_RE.match(value):
        value = "https://" + value
    host = urlparse(value).netloc or ""
    # Drop credentials / port if present.
    host = host.split("@")[-1].split(":")[0]
    host = host.removeprefix("www.")
    return host.strip(".")


def normalize_phone(raw: str | None, *, country: str | None = None) -> str:
    """Best-effort E.164 formatting. Returns '' when there is no usable number.

    - Keeps an existing ``+<countrycode>`` prefix.
    - Converts a ``00`` international prefix to ``+``.
    - When a national number is given with a ``country`` hint, prepends the dial
      code (dropping a single leading trunk ``0``).
    """
    value = (raw or "").strip()
    if not value:
        return ""
    had_plus = value.startswith("+")
    intl_00 = value.startswith("00")
    digits = re.sub(r"\D", "", value)
    if not digits:
        return ""
    if had_plus:
        return "+" + digits if _plausible_e164(digits) else ""
    if intl_00:
        trimmed = digits[2:]
        return "+" + trimmed if _plausible_e164(trimmed) else ""
    dial = _DIAL_CODES.get((country or "").strip().upper())
    if dial:
        national = digits
        if national.startswith(_TRUNK_PREFIX) and len(national) > 1:
            national = national[1:]
        candidate = dial + national
        return "+" + candidate if _plausible_e164(candidate) else ""
    # No country hint and no international prefix: accept only if it already looks
    # like a full international number (>= 11 digits), else leave unformatted.
    if 11 <= len(digits) <= 15:
        return "+" + digits
    return ""


def _plausible_e164(digits: str) -> bool:
    # E.164 allows up to 15 digits; require at least 8 to avoid junk.
    return 8 <= len(digits) <= 15


def split_person_name(person: str | None) -> tuple[str, str]:
    """Split a full name into (first, last). Handles single tokens gracefully."""
    parts = [p for p in re.split(r"\s+", (person or "").strip()) if p]
    if not parts:
        return "", ""
    if len(parts) == 1:
        return parts[0], ""
    return parts[0], parts[-1]


_COMPANY_SUFFIXES = {
    "inc", "inc.", "llc", "l.l.c.", "ltd", "ltd.", "limited", "corp", "corp.",
    "co", "co.", "gmbh", "sarl", "sas", "sa", "srl", "bv", "plc", "pty",
}


def normalize_company(raw: str | None) -> str:
    """Trim and collapse whitespace in a company name (keeps legal suffixes)."""
    value = re.sub(r"\s+", " ", (raw or "").strip())
    return value


def company_dedupe_key(raw: str | None) -> str:
    """A loose key for matching the same company written slightly differently."""
    value = normalize_company(raw).lower()
    value = re.sub(r"[.,]", "", value)
    tokens = [t for t in value.split() if t not in _COMPANY_SUFFIXES]
    return " ".join(tokens)
