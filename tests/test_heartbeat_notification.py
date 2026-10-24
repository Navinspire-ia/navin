# Copyright (c) 2026-present Navinspire IA
# SPDX-License-Identifier: AGPL-3.0-only

import inspect

from navin.cli import commands
from navin.cli.commands import _heartbeat_notification


def test_report_becomes_title_and_detail():
    title, detail = _heartbeat_notification(
        "**Navin Tenders: 17 GO notices awaiting review - Atelier Demo**\n\n| Notice | Score |\n"
    )
    assert title == "Navin Tenders: 17 GO notices awaiting review - Atelier Demo"
    assert detail == "| Notice | Score |"


def test_empty_report_has_a_title():
    assert _heartbeat_notification("  \n") == ("Heartbeat", None)


def test_websocket_heartbeat_never_posts_into_a_chat():
    source = inspect.getsource(commands)
    block = source[source.index('logger.info("Heartbeat: completed, delivering response")'):]
    block = block[: block.index("await _deliver_to_channel(")]
    assert 'if channel == "websocket"' in block
    assert "publish_notification(" in block
