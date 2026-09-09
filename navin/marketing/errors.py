# Copyright (c) 2026-present Navinspire IA
# SPDX-License-Identifier: AGPL-3.0-only

"""Marketing desk errors."""

from __future__ import annotations


class MarketingError(Exception):
    """User-facing marketing failure with an HTTP status."""

    def __init__(self, message: str, *, status: int = 400) -> None:
        super().__init__(message)
        self.message = message
        self.status = status
