# Copyright (c) 2026-present Navinspire IA
# SPDX-License-Identifier: AGPL-3.0-only

"""External scanner registry and normalized adapters for Navin Security."""

from navin.security.scanners.registry import (
    REGISTRY,
    RUN_STATUSES,
    Permissions,
    ScannerRun,
    ScannerSpec,
    ScannerStatus,
    available_scanner_ids,
    detect_scanner,
    detect_scanners,
    detect_scanners_async,
    get_scanner,
    run_scanner,
    run_scanner_async,
)

__all__ = [
    "REGISTRY",
    "RUN_STATUSES",
    "Permissions",
    "ScannerRun",
    "ScannerSpec",
    "ScannerStatus",
    "available_scanner_ids",
    "detect_scanner",
    "detect_scanners",
    "detect_scanners_async",
    "get_scanner",
    "run_scanner",
    "run_scanner_async",
]
