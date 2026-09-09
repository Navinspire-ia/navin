# Copyright (c) 2026-present Navinspire IA
# SPDX-License-Identifier: AGPL-3.0-only

"""Chat channels module with plugin architecture."""

from navin.channels.base import BaseChannel
from navin.channels.manager import ChannelManager

__all__ = ["BaseChannel", "ChannelManager"]
