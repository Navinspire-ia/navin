# Copyright (c) 2026-present Navinspire IA
# SPDX-License-Identifier: AGPL-3.0-only

"""Message bus module for decoupled channel-agent communication."""

from navin.bus.events import InboundMessage, OutboundMessage
from navin.bus.queue import MessageBus

__all__ = ["MessageBus", "InboundMessage", "OutboundMessage"]
