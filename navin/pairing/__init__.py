# Copyright (c) 2026-present Navinspire IA
# SPDX-License-Identifier: AGPL-3.0-only

"""Pairing module for DM sender approval."""

from navin.pairing.store import (
    approve_code,
    clear_channel,
    deny_code,
    format_expiry,
    format_pairing_reply,
    generate_code,
    get_approved,
    handle_pairing_command,
    is_approved,
    list_pending,
    revoke,
    revoke_channel,
)

# Metadata keys used by channels and commands to tag pairing-related messages.
PAIRING_CODE_META_KEY = "_pairing_code"
PAIRING_COMMAND_META_KEY = "_pairing_command"

__all__ = [
    "approve_code",
    "clear_channel",
    "deny_code",
    "format_expiry",
    "format_pairing_reply",
    "generate_code",
    "get_approved",
    "handle_pairing_command",
    "is_approved",
    "list_pending",
    "revoke",
    "revoke_channel",
    "PAIRING_CODE_META_KEY",
    "PAIRING_COMMAND_META_KEY",
]
