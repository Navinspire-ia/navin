"""Agent publication receipts distinguish a manual handoff from a real send."""

from __future__ import annotations

import json
from unittest.mock import Mock

import pytest

from navin.agent.tools.marketing import MarketingTool
from navin.marketing import publish
from navin.marketing.store import MarketingStore
from navin.webui import marketing_desk_api

POST_TEXT = "Fictitious launch.\nJoin the test community."
TRACKED_LINK = (
    "https://example.invalid/product?utm_source=youtube&utm_medium=social"
    "&utm_campaign=navin&utm_content=cnt-handoff"
)
COPY_TEXT = f"{POST_TEXT}\n\n{TRACKED_LINK}"


@pytest.fixture
def desk(tmp_path, monkeypatch):
    store = MarketingStore(tmp_path / "marketing")
    store.save_product({"name": "Fictitious test", "site": "https://example.invalid/product"})
    store.upsert_content({
        "id": "cnt-handoff", "channel": "youtube", "status": "ready", "body": POST_TEXT,
    })
    wire = Mock(side_effect=AssertionError("No network is permitted in this test"))
    monkeypatch.setattr(marketing_desk_api, "_store", lambda: store)
    monkeypatch.setattr(marketing_desk_api, "snapshot", lambda _: {})
    monkeypatch.setattr(publish, "http_request", wire)
    monkeypatch.setattr(publish, "_navin_telegram_token", lambda: "")
    monkeypatch.setattr(publish, "_email_ready", lambda: False)
    return store, wire


@pytest.mark.asyncio
async def test_manual_publication_returns_exact_copy_handoff_without_claiming_a_send(desk):
    store, wire = desk
    result = await MarketingTool().execute(action="publish", id="cnt-handoff")
    assert str(result) == (
        "publish: 0 sent, 1 manual, 0 failed, 0 preview, 0 waiting\n"
        "- manual youtube cnt-handoff: marked published in the desk; "
        "copy and paste this text to publish:\n" + COPY_TEXT
    )
    wire.assert_not_called()
    saved = store.get_content("cnt-handoff")
    assert saved["status"] == "published"
    assert saved["receipt"]["mode"] == "manual"
    assert saved["publish_text"] == COPY_TEXT
    assert result.is_error is False


@pytest.mark.asyncio
async def test_webhook_confirmed_send_on_the_same_channel_is_reported_as_sent(desk):
    store, wire = desk
    store.save_settings({"publish": {"webhook": {"enabled": True, "url": "https://example.invalid/bridge"}}})
    wire.side_effect = None
    wire.return_value = (200, {}, b'{"id":"confirmed-post","url":"https://example.invalid/posts/confirmed-post"}')
    result = await MarketingTool().execute(action="publish", id="cnt-handoff")
    assert str(result) == (
        "publish: 1 sent, 0 manual, 0 failed, 0 preview, 0 waiting\n"
        "- sent youtube cnt-handoff https://example.invalid/posts/confirmed-post"
    )
    wire.assert_called_once()
    assert wire.call_args.args[:2] == ("POST", "https://example.invalid/bridge")
    assert json.loads(wire.call_args.args[3])["text"] == COPY_TEXT
    saved = store.get_content("cnt-handoff")
    assert saved["receipt"]["mode"] == "api"
    assert saved["remote_id"] == "confirmed-post"
    assert result.is_error is False


@pytest.mark.asyncio
async def test_manual_preview_remains_an_exact_preview_without_marking_publication(desk):
    store, wire = desk
    before = store.get_content("cnt-handoff")
    result = await MarketingTool().execute(action="publish", id="cnt-handoff", dry_run="true")
    assert str(result) == (
        "publish: 0 sent, 0 manual, 0 failed, 1 preview, 0 waiting\n"
        "- preview via manual:\n" + COPY_TEXT
    )
    wire.assert_not_called()
    assert store.get_content("cnt-handoff") == before


@pytest.mark.asyncio
async def test_instagram_container_returns_exact_pending_message_without_claiming_a_send(desk):
    store, wire = desk
    store.save_settings({"publish": {"instagram": {"enabled": True, "instagram_user_id": "123", "auth_mode": "instagram"}}})
    store.save_secret("instagram_access_token", "fixture-token")
    store.upsert_content({"id": "cnt-handoff", "channel": "instagram", "media_type": "image", "media_url": "https://media.example.com/image.jpg"})
    wire.side_effect = [
        (200, {}, b'{"data":[{"quota_usage":0,"config":{"quota_total":100,"quota_duration":86400}}]}'),
        (200, {}, b'{"id":"container-123"}'),
    ]
    result = await MarketingTool().execute(action="publish", id="cnt-handoff")
    assert str(result) == (
        "publish: 0 sent, 1 publishing, 0 manual, 0 failed, 0 preview, 0 waiting\n"
        "- publishing instagram cnt-handoff: container; publication is not confirmed"
    )
    assert store.get_content("cnt-handoff")["status"] == "publishing"
    assert wire.call_count == 2


@pytest.mark.asyncio
async def test_content_options_attaches_the_actual_creative_and_resets_approval(desk):
    store, wire = desk
    media = store.root / "assets" / "test.png"
    media.parent.mkdir(parents=True, exist_ok=True)
    from PIL import Image

    Image.new("RGB", (48, 48), (20, 60, 120)).save(media)
    creative = store.upsert_creative({"id": "creative-fixture", "kind": "image", "path": str(media), "status": "ready"})
    row = store.upsert_content({"id": "cnt-handoff", "status": "approved", "publish_consent": True})
    result = await MarketingTool().execute(action="content-options", id=row["id"], content_options={
        "revision": row["updated_at"], "creative_id": creative["id"], "body": "Reviewed new text.",
    })
    assert not result.is_error, str(result)
    saved = store.get_content(row["id"])
    assert saved["creative_id"] == creative["id"] and saved["body"] == "Reviewed new text."
    assert saved["status"] == "draft" and saved["publish_consent"] is False
    assert "creative_id=creative-fixture" in str(result)
    wire.assert_not_called()


@pytest.mark.asyncio
async def test_agent_cannot_invent_the_users_tiktok_consent(desk):
    store, wire = desk
    row = store.get_content("cnt-handoff")
    result = await MarketingTool().execute(action="content-options", id=row["id"], content_options={
        "revision": row["updated_at"], "publish_consent": True,
    })
    assert result.is_error
    assert str(result) == "TikTok consent must be confirmed by the user in Studio after reviewing the post"
    assert store.get_content(row["id"]) == row
    wire.assert_not_called()
