# Copyright (c) 2026-present Navinspire IA
# SPDX-License-Identifier: AGPL-3.0-only

"""A gateway bound beyond loopback must demand a token or issue secret.

Binding a LAN or WSL address is how the UI is shared with another machine;
without a token that same reachability belongs to everyone on the network.
"""

from __future__ import annotations

import pytest
from pydantic import ValidationError

from navin.channels.websocket import WebSocketConfig


def _config(**overrides) -> dict:
    values: dict = {
        "path": "/ws",
        "token_issue_path": "/ws/token",
    }
    values.update(overrides)
    return values


def test_loopback_binds_need_no_token() -> None:
    for host in ("127.0.0.1", "localhost", "::1"):
        config = WebSocketConfig.model_validate(_config(host=host))
        assert config.host == host


def test_lan_bind_without_token_is_rejected() -> None:
    for host in ("0.0.0.0", "192.168.1.20", "10.0.0.5", "::"):
        with pytest.raises(ValidationError):
            WebSocketConfig.model_validate(_config(host=host))


def test_lan_bind_with_token_is_accepted() -> None:
    config = WebSocketConfig.model_validate(_config(host="192.168.1.20", token="s3cret"))
    assert config.token == "s3cret"


def test_lan_bind_with_token_issue_secret_is_accepted() -> None:
    config = WebSocketConfig.model_validate(
        _config(host="0.0.0.0", token_issue_secret="s3cret")
    )
    assert config.token_issue_secret == "s3cret"
