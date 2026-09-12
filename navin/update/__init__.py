# Copyright (c) 2026-present Navinspire IA
# SPDX-License-Identifier: AGPL-3.0-only

"""Signed update support for the desktop app and the packaged CLI."""

from navin.update.service import (
    UpdateError,
    apply_cli_update,
    check_for_update,
    download_update,
    install_update,
    start_update_download,
    update_status,
    updates_configured,
)

__all__ = [
    "UpdateError",
    "apply_cli_update",
    "check_for_update",
    "download_update",
    "install_update",
    "start_update_download",
    "update_status",
    "updates_configured",
]
