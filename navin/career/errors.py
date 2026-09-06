"""Career desk errors."""

from __future__ import annotations


class CareerError(Exception):
    """User-facing career failure with an HTTP status."""

    def __init__(self, message: str, *, status: int = 400) -> None:
        super().__init__(message)
        self.message = message
        self.status = status
