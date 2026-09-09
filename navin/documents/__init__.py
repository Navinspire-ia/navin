# Copyright (c) 2026-present Navinspire IA
# SPDX-License-Identifier: AGPL-3.0-only

"""Document conversion helpers.

Kept free of heavy imports on purpose: the agent runs these modules straight
from a shell (``python3 -m navin.documents.html2pptx``) inside user
workspaces, where the full runtime dependency set may not be installed.
"""
