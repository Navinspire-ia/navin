# Copyright (c) 2026-present Navinspire IA
# SPDX-License-Identifier: AGPL-3.0-only

"""Stable public finding contract.

Keep this module as the import surface for integrations. The concrete models
live in ``models`` so reports and tools share one schema.
"""

from navin.seo.models import Confidence, Evidence, Finding, Severity

__all__ = ["Confidence", "Evidence", "Finding", "Severity"]
