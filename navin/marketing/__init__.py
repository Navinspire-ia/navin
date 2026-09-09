# Copyright (c) 2026-present Navinspire IA
# SPDX-License-Identifier: AGPL-3.0-only

"""Navin Marketing: understand, plan, create, measure, improve."""

from navin.marketing.errors import MarketingError
from navin.marketing.visual_qa import (
    VisualQAPolicy,
    format_markdown_report,
    run_visual_qa,
    write_visual_qa_report,
)

__all__ = [
    "MarketingError",
    "VisualQAPolicy",
    "format_markdown_report",
    "run_visual_qa",
    "write_visual_qa_report",
]
