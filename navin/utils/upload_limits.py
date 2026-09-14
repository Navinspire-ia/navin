# Copyright (c) 2026-present Navinspire IA
# SPDX-License-Identifier: AGPL-3.0-only

"""Shared byte limits for user-provided files and their transport envelope."""

MAX_UPLOAD_FILE_BYTES = 200 * 1024 * 1024
MAX_UPLOAD_TOTAL_BYTES = 200 * 1024 * 1024
# Base64 adds one third to the file size; leave room for text and JSON fields.
MAX_UPLOAD_REQUEST_BYTES = 288 * 1024 * 1024
