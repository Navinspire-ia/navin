# Copyright (c) 2026-present Navinspire IA
# SPDX-License-Identifier: AGPL-3.0-only

"""The advertised upload ceiling must survive transport, storage and reading."""

from __future__ import annotations

import base64
from pathlib import Path
from unittest.mock import Mock

import pytest

from navin.api.server import create_app
from navin.channels.websocket import WebSocketConfig
from navin.security.workspace_access import default_workspace_scope
from navin.utils.document import extract_documents
from navin.utils.media_decode import FileSizeExceeded, save_base64_data_url
from navin.webui.attachment_ingress import store_inbound_attachments
from navin.webui.file_preview import file_download_payload
from navin.webui.ingress_policy import DEFAULT_WEBUI_INGRESS_POLICY, AttachmentIngressLimits

MIB = 1024 * 1024


@pytest.mark.parametrize("config", [{}, {"max_message_bytes": 37_748_736}, {"maxMessageBytes": 37_748_736}])
def test_new_and_saved_defaults_transport_a_100_mb_file(config):
    channel = WebSocketConfig(**config)
    policy = DEFAULT_WEBUI_INGRESS_POLICY
    advertised = policy.bootstrap_limits(max_frame_bytes=channel.max_message_bytes)
    assert advertised["attachments"]["max_file_bytes"] == 100 * MIB
    assert advertised["attachments"]["max_total_bytes"] == 100 * MIB
    assert channel.max_message_bytes >= policy.minimum_full_policy_frame_bytes()
    assert create_app(Mock())._client_max_size >= policy.minimum_full_policy_frame_bytes()


def test_custom_transport_limit_is_preserved():
    assert WebSocketConfig(max_message_bytes=4 * MIB).max_message_bytes == 4 * MIB


@pytest.mark.parametrize("size_mb", [13, 100])
def test_large_document_is_stored_readable_and_downloadable(tmp_path: Path, size_mb: int):
    marker = b"Navin large document upload regression\n"
    size = size_mb * MIB
    data_url = "data:text/plain;base64," + base64.b64encode(marker + b" " * (size - len(marker))).decode()
    paths, reason = store_inbound_attachments(
        [{"name": "large-report.txt", "data_url": data_url}],
        media_dir=tmp_path,
        logger=Mock(),
    )
    del data_url
    assert reason is None
    assert len(paths) == 1
    saved = Path(paths[0])
    assert saved.stat().st_size == size
    content, images = extract_documents("Read this document", paths)
    assert marker.decode().strip() in content
    assert images == []
    body, _, filename, _ = file_download_payload(
        str(saved), scope=default_workspace_scope(tmp_path, True),
    )
    assert len(body) == size
    assert body.startswith(marker)
    assert filename.endswith("_large-report.txt")


def test_one_byte_above_100_mb_is_rejected_without_writing(tmp_path: Path):
    # These lengths share a base64 block, so the decoded-byte guard is needed.
    data_url = "data:text/plain;base64," + base64.b64encode(b" " * (100 * MIB + 1)).decode()
    paths, reason = store_inbound_attachments(
        [{"name": "oversized.txt", "data_url": data_url}], media_dir=tmp_path, logger=Mock(),
    )
    assert reason == "size"
    assert paths == []
    assert list(tmp_path.iterdir()) == []


def test_oversized_base64_is_rejected_before_decoding(tmp_path: Path, monkeypatch):
    decoder = Mock(side_effect=AssertionError("oversized payload must not be decoded"))
    monkeypatch.setattr("navin.utils.media_decode.base64.b64decode", decoder)
    with pytest.raises(FileSizeExceeded):
        save_base64_data_url("data:text/plain;base64," + "YQ==" * 8, tmp_path, max_bytes=8)
    decoder.assert_not_called()
    assert list(tmp_path.iterdir()) == []


@pytest.mark.parametrize("mime", ["text/plain", "audio/mpeg", "video/mp4"])
@pytest.mark.parametrize("second_size,expected", [(6, None), (7, "total_size")])
def test_mixed_batches_share_one_total_and_roll_back(tmp_path: Path, mime: str, second_size: int, expected):
    def item(content_type: str, size: int):
        return {"data_url": f"data:{content_type};base64," + base64.b64encode(b"a" * size).decode()}

    paths, reason = store_inbound_attachments(
        [item("text/plain", 6), item(mime, second_size)],
        media_dir=tmp_path,
        logger=Mock(),
        limits=AttachmentIngressLimits(max_file_bytes=8, max_total_bytes=12),
    )
    assert reason == expected
    assert len(paths) == (2 if expected is None else 0)
    assert len(list(tmp_path.iterdir())) == len(paths)
