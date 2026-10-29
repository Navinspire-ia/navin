# Copyright (c) 2026-present Navinspire IA
# SPDX-License-Identifier: AGPL-3.0-only

"""What the verification engine is allowed to send, and to whom.

Three rules, enforced before any byte leaves the machine:

- only read-only methods are ever sent (GET, HEAD, OPTIONS);
- the target must be on the explicit list of authorized targets, matched on
  scheme, host and port (a look-alike host or user-info trick never matches);
- a reproduction (the ``exploitable`` status) also needs an isolated
  environment and a human approval.
"""

from __future__ import annotations

from dataclasses import dataclass
from urllib.parse import urlsplit

SAFE_METHODS: frozenset[str] = frozenset({"GET", "HEAD", "OPTIONS"})
_DEFAULT_PORTS = {"http": 80, "https": 443}


class VerificationRefusedError(PermissionError):
    """The request is outside what the user authorized."""


@dataclass(frozen=True)
class Authorization:
    """Scope the user handed over for this audit."""

    targets: tuple[str, ...] = ()
    isolated: bool = False
    human_approval: bool = False

    def allows(self, url: str) -> bool:
        parts = urlsplit(url)
        host = (parts.hostname or "").lower()
        if parts.scheme not in _DEFAULT_PORTS or not host:
            return False
        port = parts.port or _DEFAULT_PORTS[parts.scheme]
        for entry in self.targets:
            allowed = urlsplit(entry if "://" in entry else f"//{entry}")
            if (allowed.hostname or "").lower() != host:
                continue
            if allowed.scheme and allowed.scheme != parts.scheme:
                continue
            # No explicit port means the default port of the scheme, never "any port".
            if (allowed.port or _DEFAULT_PORTS.get(allowed.scheme or parts.scheme)) != port:
                continue
            return True
        return False


def check_request(method: str, url: str, auth: Authorization, *, reproduction: bool = False) -> str:
    """Return the normalized method, or raise :class:`VerificationRefusedError` with the reason."""
    verb = str(method or "").strip().upper()
    if verb not in SAFE_METHODS:
        raise VerificationRefusedError(
            f"{verb or 'empty method'} is refused: verification only sends {', '.join(sorted(SAFE_METHODS))}"
        )
    if not auth.allows(url):
        raise VerificationRefusedError(f"{url} is not on the list of authorized targets")
    if reproduction:
        if not auth.isolated:
            raise VerificationRefusedError("a reproduction needs an isolated environment with synthetic data")
        if not auth.human_approval:
            raise VerificationRefusedError("a reproduction needs a human approval")
    return verb
