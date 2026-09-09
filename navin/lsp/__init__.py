# Copyright (c) 2026-present Navinspire IA
# SPDX-License-Identifier: AGPL-3.0-only

"""Language Server Protocol integration.

Provides the semantic layer the code index cannot give on its own: types on
hover, type-resolved definitions and references, and safe renames computed by a
real compiler front end.

Servers are declared in ``servers.json`` (pyright, pylsp, typescript-language-
server, gopls, rust-analyzer) and started lazily on first use. When no server is
installed for a language, callers fall back to :mod:`navin.index`, which needs
no external tooling.
"""

from navin.lsp.client import LspClient, LspError
from navin.lsp.manager import LspLocation, LspManager, available_servers

__all__ = [
    "LspClient",
    "LspError",
    "LspLocation",
    "LspManager",
    "available_servers",
]
