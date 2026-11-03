# Copyright (c) 2026-present Navinspire IA
# SPDX-License-Identifier: AGPL-3.0-only

"""Microsoft Teams: personal chats, group chats and channel threads per group_policy."""

from __future__ import annotations

import asyncio
import time
from types import SimpleNamespace
from unittest.mock import AsyncMock

import httpx
import pytest

from navin.bus.events import OutboundMessage
from navin.bus.queue import MessageBus
from navin.channels import msteams
from navin.channels._setup import CHANNEL_SETUP_SPECS
from navin.channels.msteams import ConversationRef, MSTeamsChannel, MSTeamsConfig

SERVICE_URL = "https://smba.trafficmanager.net/emea/"
BOT_ID = "28:bot-app"


@pytest.fixture(autouse=True)
def _workspace(tmp_path, monkeypatch):
    monkeypatch.setattr(msteams, "get_workspace_path", lambda: tmp_path)


def _channel(**config) -> tuple[MSTeamsChannel, MessageBus]:
    bus = MessageBus()
    settings = {"enabled": True, "app_id": "bot-app", "app_password": "pw", "allow_from": ["aad-alice"]}
    settings.update(config)
    return MSTeamsChannel(MSTeamsConfig(**settings), bus), bus


def _activity(conversation_id: str, conversation_type: str, text: str, *, mentions=(), sender="aad-alice",
              activity_id: str = "1700000000001") -> dict:
    return {
        "type": "message",
        "id": activity_id,
        "serviceUrl": SERVICE_URL,
        "from": {"id": f"29:{sender}", "aadObjectId": sender, "name": "Alice Martin"},
        "recipient": {"id": BOT_ID, "name": "Navin"},
        "conversation": {"id": conversation_id, "conversationType": conversation_type},
        "text": text,
        "entities": [
            {"type": "mention", "mentioned": {"id": mid, "name": name}, "text": f"<at>{name}</at>"}
            for mid, name in mentions
        ] + [{"type": "clientInfo", "locale": "fr-FR"}],
        "channelData": {"tenant": {"id": "tenant-1"}},
    }


def _inbound(bus: MessageBus) -> list:
    items = []
    while not bus.inbound.empty():
        items.append(bus.inbound.get_nowait())
    return items


def test_group_chat_answers_only_when_the_bot_is_mentioned():
    async def run():
        channel, bus = _channel()
        silent = _activity("19:group@thread.v2", "groupChat", "lunch at noon?",
                           mentions=[("29:bob", "Bob")])
        await channel._handle_activity(silent)
        assert _inbound(bus) == [] and "19:group@thread.v2" not in channel._conversation_refs

        asked = _activity("19:group@thread.v2", "groupChat", "<at>Navin</at> deploy the site, ask <at>Bob</at>",
                          mentions=[(BOT_ID, "Navin"), ("29:bob", "Bob")])
        await channel._handle_activity(asked)
        (msg,) = _inbound(bus)
        assert msg.channel == "msteams" and msg.chat_id == "19:group@thread.v2"
        assert msg.content == "deploy the site, ask Bob"  # only the bot's own mention goes
        assert msg.metadata["is_group"] is True and msg.metadata["sender_name"] == "Alice Martin"
        assert msg.metadata["msteams"]["conversation_type"] == "groupChat"

    asyncio.run(run())


def test_group_sender_still_goes_through_allow_from():
    async def run():
        channel, bus = _channel(group_policy="open")
        stranger = _activity("19:group@thread.v2", "groupChat", "<at>Navin</at> rm -rf",
                             mentions=[(BOT_ID, "Navin")], sender="aad-mallory")
        await channel._handle_activity(stranger)
        assert _inbound(bus) == [] and channel._conversation_refs == {}

    asyncio.run(run())


def test_group_ref_survives_pruning_and_a_restart_unless_policy_is_off():
    async def run():
        channel, bus = _channel()
        await channel._handle_activity(_activity(
            "19:group@thread.v2", "groupChat", "<at>Navin</at> status?", mentions=[(BOT_ID, "Navin")]))
        assert _inbound(bus)
        assert channel._conversation_refs["19:group@thread.v2"].conversation_type == "groupChat"
        reloaded, _ = _channel()
        assert "19:group@thread.v2" in reloaded._conversation_refs
        dm_only, dm_bus = _channel(group_policy="off")
        assert "19:group@thread.v2" not in dm_only._conversation_refs  # DM-only keeps today's pruning
        await dm_only._handle_activity(_activity(
            "19:group@thread.v2", "groupChat", "<at>Navin</at> status?", mentions=[(BOT_ID, "Navin")]))
        assert _inbound(dm_bus) == []

    asyncio.run(run())


def test_channel_message_is_answered_in_its_thread():
    async def run():
        channel, bus = _channel(group_policy="open")
        thread = "19:general@thread.tacv2;messageid=1700000000000"
        await channel._handle_activity(_activity(thread, "channel", "build failed on main",
                                                 activity_id="1700000000005"))
        (msg,) = _inbound(bus)
        assert msg.chat_id == thread and msg.content == "build failed on main"
        assert msg.metadata["is_group"] is True

        response = httpx.Response(200, json={"id": "reply-1"}, request=httpx.Request("POST", SERVICE_URL))
        post = AsyncMock(return_value=response)
        channel._http = SimpleNamespace(post=post)
        channel._get_access_token = AsyncMock(return_value="token")
        await channel.send(OutboundMessage(channel="msteams", chat_id=thread, content="Looking at it"))
        url = post.await_args.args[0]
        payload = post.await_args.kwargs["json"]
        assert url == f"{SERVICE_URL.rstrip('/')}/v3/conversations/{thread}/activities"
        assert payload == {"type": "message", "text": "Looking at it", "replyToId": "1700000000005"}

    asyncio.run(run())


def test_channel_root_post_without_thread_id_gets_the_thread_suffix():
    ref = ConversationRef(service_url=SERVICE_URL, conversation_id="19:general@thread.tacv2",
                          activity_id="1700000000009", conversation_type="channel", updated_at=time.time())
    assert MSTeamsChannel._reply_conversation_id(ref) == "19:general@thread.tacv2;messageid=1700000000009"
    group = ConversationRef(service_url=SERVICE_URL, conversation_id="19:group@thread.v2",
                            activity_id="1700000000009", conversation_type="groupChat")
    assert MSTeamsChannel._reply_conversation_id(group) == "19:group@thread.v2"


def test_personal_chat_is_unchanged():
    async def run():
        channel, bus = _channel(group_policy="off")
        await channel._handle_activity(_activity("a:personal-1", "personal", "hello"))
        (msg,) = _inbound(bus)
        assert msg.content == "hello" and msg.metadata["is_group"] is False

    asyncio.run(run())


def test_group_policy_is_a_settings_field():
    assert MSTeamsConfig().group_policy == "mention"
    assert MSTeamsConfig.model_validate({"groupPolicy": "open"}).group_policy == "open"
    with pytest.raises(ValueError):
        MSTeamsConfig(group_policy="allowlist")
    field = CHANNEL_SETUP_SPECS["msteams"].fields["groupPolicy"]
    assert field.kind == "enum" and set(field.choices) == {"mention", "open", "off"}
