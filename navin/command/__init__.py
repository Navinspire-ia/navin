# Copyright (c) 2026-present Navinspire IA
# SPDX-License-Identifier: AGPL-3.0-only

"""Slash command routing and built-in handlers."""

from navin.command.builtin import register_builtin_commands
from navin.command.router import CommandContext, CommandRouter

__all__ = ["CommandContext", "CommandRouter", "register_builtin_commands"]
