# Copyright (c) 2026-present Navinspire IA
# SPDX-License-Identifier: AGPL-3.0-only

"""Durable meeting records and integrations."""

from navin.meetings.store import MeetingStore, default_meeting_store

__all__ = ["MeetingStore", "default_meeting_store"]
