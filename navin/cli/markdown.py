# Copyright (c) 2026-present Navinspire IA
# SPDX-License-Identifier: AGPL-3.0-only

"""Restrained response accents shared by streamed and final CLI answers."""

from rich.markdown import Markdown
from rich.theme import Theme

_RESPONSE_THEME = Theme({
    "markdown.h1": "bold bright_blue",
    "markdown.h2": "bold bright_blue",
    "markdown.h3": "bold cyan",
    "markdown.strong": "bold bright_blue",
    "markdown.item.bullet": "cyan",
    "markdown.item.number": "cyan",
    "markdown.code": "yellow",
    "markdown.link": "underline cyan",
})


class ResponseMarkdown(Markdown):
    def __rich_console__(self, console, options):
        with console.use_theme(_RESPONSE_THEME):
            yield from super().__rich_console__(console, options)
