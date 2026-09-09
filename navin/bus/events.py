# Copyright (c) 2026-present Navinspire IA
# SPDX-License-Identifier: AGPL-3.0-only

"""Event types for the message bus."""

from dataclasses import dataclass, field
from datetime import datetime
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from navin.bus.outbound_events import OutboundEvent

# Optional ``OutboundMessage.metadata`` key for structured, channel-agnostic UI
# payloads. Value is JSON-serializable with at least ``kind``; rich clients may
# render it and other channels may ignore unknown keys.
OUTBOUND_META_AGENT_UI = "_agent_ui"

# Internal-only inbound metadata used by in-process channels to ask the agent
# loop to update runtime state without going through a user session.
INBOUND_META_RUNTIME_CONTROL = "_runtime_control"
RUNTIME_CONTROL_ACK = "_ack"
RUNTIME_CONTROL_MCP_RELOAD = "mcp_reload"
RUNTIME_CONTROL_EXEC_POLICY_RELOAD = "exec_policy_reload"
# An answer to a pending approval. It rides the runtime-control route because
# that is the only inbound path handled while a tool call is still running: a
# normal message would be parked for mid-turn injection and never reach the
# suspended tool.
RUNTIME_CONTROL_APPROVAL_DECISION = "approval_decision"
# A reconnecting client asking what is still waiting on it. Without this, a
# browser refresh loses the card while the tool stays suspended.
RUNTIME_CONTROL_APPROVALS_QUERY = "approvals_query"
# Same round trip for a product choice: the agent stopped because it does not
# know which path to take, and a normal user message would never reach the
# suspended tool.
RUNTIME_CONTROL_CHOICE_ANSWER = "choice_answer"
RUNTIME_CONTROL_CHOICES_QUERY = "choices_query"
# Same idea for background subagents: their cards live in browser memory only,
# so without a replay a refresh hides work that is still running.
RUNTIME_CONTROL_SUBAGENTS_QUERY = "subagents_query"
# Multitask: dispatch a queued composer prompt to a parallel subagent instead
# of waiting for the current turn to finish.
RUNTIME_CONTROL_MULTITASK_SPAWN = "multitask_spawn"

# Optional ``InboundMessage.metadata`` key naming the model preset to use for
# this turn only (Cursor-style per-conversation model choice). The global
# default preset is left untouched; unknown names fall back to the default.
INBOUND_META_MODEL_PRESET = "model_preset"


@dataclass
class InboundMessage:
    """Message received from a chat channel."""

    channel: str  # telegram, discord, slack, whatsapp
    sender_id: str  # User identifier
    chat_id: str  # Chat/channel identifier
    content: str  # Message text
    timestamp: datetime = field(default_factory=datetime.now)
    media: list[str] = field(default_factory=list)  # Media URLs
    metadata: dict[str, Any] = field(default_factory=dict)  # Channel-specific data
    session_key_override: str | None = None  # Optional override for thread-scoped sessions

    @property
    def session_key(self) -> str:
        """Unique key for session identification."""
        return self.session_key_override or f"{self.channel}:{self.chat_id}"


@dataclass
class OutboundMessage:
    """Message to send to a chat channel.

    ``event`` carries internal runtime/UI semantics. ``metadata`` is reserved
    for channel routing context (``message_id``, thread ids, etc.) and optional
    ``OUTBOUND_META_AGENT_UI`` blobs for rich clients.
    """

    channel: str
    chat_id: str
    content: str
    reply_to: str | None = None
    media: list[str] = field(default_factory=list)
    metadata: dict[str, Any] = field(default_factory=dict)
    buttons: list[list[str]] = field(default_factory=list)
    event: "OutboundEvent | None" = None
