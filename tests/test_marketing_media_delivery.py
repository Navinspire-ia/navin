from __future__ import annotations

from urllib.parse import parse_qs, urlsplit

import pytest
from PIL import Image

from navin.marketing.assets import resolve_asset
from navin.marketing.errors import MarketingError
from navin.marketing.media_delivery import resolve_publish_media
from navin.marketing.publication_options import update_publication_options
from navin.marketing.store import MarketingStore


@pytest.mark.parametrize("channel", ["instagram", "tiktok"])
def test_generated_png_has_real_public_jpeg_and_keeps_original(tmp_path, channel):
    store = MarketingStore(tmp_path / "marketing")
    store.save_settings({"media_base_url": "https://navin.example.com"})
    assets = store.root / "assets"
    assets.mkdir()
    original = assets / "generated.png"
    Image.new("RGBA", (1500, 1200), (20, 70, 130, 150)).save(original)
    row = {"id": "draft-one", "channel": channel, "media_type": "image", "media_path": str(original)}
    before = {str(path.relative_to(store.root)): path.read_bytes() for path in store.root.rglob("*") if path.is_file()}
    planned = resolve_publish_media(store, row, create=False)
    assert planned["mime_type"] == "image/jpeg"
    after = {str(path.relative_to(store.root)): path.read_bytes() for path in store.root.rglob("*") if path.is_file()}
    assert after == before
    prepared = resolve_publish_media(store, row, create=True)
    assert prepared["media_url"] == planned["media_url"]
    name = parse_qs(urlsplit(prepared["media_url"]).query)["name"][0]
    downloadable = resolve_asset(store, name)
    assert downloadable is not None
    with Image.open(downloadable) as image:
        assert image.format == "JPEG" and image.mode == "RGB"
        if channel == "tiktok":
            assert max(image.size) <= 1080
    assert original.read_bytes() == before["assets/generated.png"]


def test_private_file_cannot_be_published_by_supplying_its_path(tmp_path):
    store = MarketingStore(tmp_path / "marketing")
    private = tmp_path / "private.jpg"
    Image.new("RGB", (20, 20)).save(private)
    with pytest.raises(MarketingError, match="Import"):
        resolve_publish_media(store, {"media_path": str(private)}, create=True)


def test_editing_copy_invalidates_old_consent_and_approval(tmp_path):
    store = MarketingStore(tmp_path)
    row = store.upsert_content({"id": "one", "channel": "tiktok", "body": "Before", "status": "approved", "publish_consent": True, "music_usage_confirmed": True})
    updated = update_publication_options(store, "one", {"revision": row["updated_at"], "body": "After"})
    assert updated["status"] == "draft"
    assert updated["publish_consent"] is False and updated["music_usage_confirmed"] is False
    with pytest.raises(MarketingError, match="changed"):
        update_publication_options(store, "one", {"revision": row["updated_at"], "body": "Stale"})
    assert store.get_content("one")["body"] == "After"


def test_a_post_in_flight_cannot_be_edited(tmp_path):
    store = MarketingStore(tmp_path)
    row = store.upsert_content({"id": "one", "channel": "instagram", "body": "Original", "status": "publishing"})
    with pytest.raises(MarketingError, match="already publishing"):
        update_publication_options(store, "one", {"revision": row["updated_at"], "body": "Changed"})
    assert store.get_content("one")["body"] == "Original"
