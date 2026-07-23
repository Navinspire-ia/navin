"""Message bus module for decoupled channel-agent communication."""

from navin.bus.events import InboundMessage, OutboundMessage
from navin.bus.queue import MessageBus

__all__ = ["MessageBus", "InboundMessage", "OutboundMessage"]
