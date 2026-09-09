"""Real desk entrypoints over scripted social transports, with no live posts."""

from __future__ import annotations

import json
import time
from collections import deque
from urllib.parse import parse_qs

import pytest
from PIL import Image

from navin.marketing import loop, publish, social_publish
from navin.marketing.errors import MarketingError
from navin.marketing.store import MarketingStore, _atomic_write


class Wire:
    def __init__(self, *steps):
        self.steps = deque(steps)
        self.calls = []

    def __call__(self, method, url, headers, body):
        self.calls.append({"method": method, "url": url, "headers": headers, "body": body})
        assert self.steps, f"Unexpected HTTP request: {method} {url}"
        wanted_method, suffix, status, response = self.steps.popleft()
        assert method == wanted_method
        assert suffix in url, (suffix, url)
        if isinstance(response, Exception):
            raise response
        response_headers = {}
        if isinstance(response, tuple):
            response_headers, response = response
        data = response if isinstance(response, bytes) else json.dumps(response).encode()
        return status, response_headers, data

    def done(self):
        assert not self.steps


def step(method, suffix, data=None, status=200):
    return method, suffix, status, data if data is not None else {}


def quota(used=0):
    return step("GET", "/123/content_publishing_limit", {"data": [{"quota_usage": used, "config": {"quota_total": 100, "quota_duration": 86400}}]})


def creator(**extra):
    return step("POST", "/creator_info/query/", {"data": {
        "creator_username": "fixture", "creator_nickname": "Fixture Account",
        "privacy_level_options": ["SELF_ONLY", "PUBLIC_TO_EVERYONE"],
        "max_video_post_duration_sec": 180, "comment_disabled": False,
        "duet_disabled": False, "stitch_disabled": False, **extra,
    }, "error": {"code": "ok"}})


@pytest.fixture
def desk(tmp_path, monkeypatch):
    store = MarketingStore(tmp_path / "marketing")
    store.save_settings({"ai_assist": False, "publish": {
        "reddit": {"enabled": True, "subreddit": "fixture", "user_agent": "test:Navin:1.0 (by /u/fixture)"},
        "instagram": {"enabled": True, "instagram_user_id": "123", "auth_mode": "instagram"},
        "tiktok": {"enabled": True}, "linkedin": {"enabled": True, "author": "urn:li:person:fixture"},
        "facebook": {"enabled": True, "page_id": "456"},
    }})
    store.save_secrets({key: f"fixture-{channel}" for channel, key in social_publish.TOKEN_KEYS.items()})
    monkeypatch.setattr(publish, "http_request", Wire())
    monkeypatch.setattr(publish, "_navin_telegram_token", lambda: "")
    monkeypatch.setattr(publish, "_email_ready", lambda: False)
    return store


def content(store, channel, **fields):
    defaults = {"channel": channel, "title": "A fixture launch", "body": "A documented update.", "status": "approved"}
    if channel == "instagram":
        defaults.update(media_type="image", media_url="https://media.example.com/test.jpg")
    if channel == "tiktok":
        defaults.update(media_type="video", media_url="https://media.example.com/test.mp4", duration_s=12,
                        privacy_level="SELF_ONLY", publish_consent=True, music_usage_confirmed=True,
                        brand_content_toggle=False, brand_organic_toggle=False)
    return store.upsert_content({**defaults, **fields})


def image_file(store, *, kind="JPEG"):
    path = store.root / "assets" / ("image.png" if kind == "PNG" else "image.jpg")
    path.parent.mkdir(parents=True, exist_ok=True)
    Image.new("RGB", (720, 960), (20, 60, 140)).save(path, format=kind)
    return path


def video_file(store, monkeypatch, *, size=64, duration=12):
    path = store.root / "assets" / "video.mp4"
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("wb") as file:
        file.truncate(size)
    monkeypatch.setattr(social_publish, "probe_video", lambda _: {"duration_s": duration, "width": 720, "height": 1280})
    return path


def tree(root):
    return {str(path.relative_to(root)): path.read_bytes() for path in root.rglob("*") if path.is_file()}


@pytest.mark.parametrize("kind", ["self", "link"])
def test_reddit_sends_selected_kind_and_preserves_real_receipt(desk, kind):
    row = content(desk, "reddit", reddit_kind=kind, reddit_url="https://example.com/document", reddit_flair_id="chosen-flair")
    wire = Wire(step("POST", "https://oauth.reddit.com/api/submit", ({"x-ratelimit-remaining": "79"}, {"json": {"errors": [], "data": {"name": "t3_fixture", "url": "https://www.reddit.com/r/fixture/comments/fixture/"}}})))
    result = publish.publish_content(desk, row["id"], http=wire)
    assert result["confirmed"] and not result["pending"]
    saved = result["content"]
    assert saved["status"] == "published" and saved["remote_id"] == "t3_fixture"
    form = parse_qs(wire.calls[0]["body"].decode())
    assert form["kind"] == [kind] and form["sr"] == ["fixture"]
    assert form["flair_id"] == ["chosen-flair"]
    assert form["text"] == [row["body"]] if kind == "self" else form["url"] == [row["reddit_url"]]
    assert wire.calls[0]["headers"]["User-Agent"] == "test:Navin:1.0 (by /u/fixture)"
    assert saved["receipt"]["quota"]["x-ratelimit-remaining"] == "79"
    wire.done()


@pytest.mark.parametrize("response,status,error", [
    ({"json": {"errors": [["RATELIMIT", "Take a break", "ratelimit"]]}}, 200, "RATELIMIT"),
    ({"error": "invalid_token"}, 401, "invalid_token"),
])
def test_reddit_errors_are_not_post_receipts(desk, response, status, error):
    row = content(desk, "reddit")
    wire = Wire(step("POST", "/api/submit", response, status))
    result = publish.publish_content(desk, row["id"], http=wire)
    assert not result["ok"] and not result["confirmed"] and not result["pending"]
    assert result["content"]["status"] == "failed"
    assert error in result["error"]
    wire.done()


def test_instagram_container_then_publish_is_not_a_premature_success(desk):
    row = content(desk, "instagram")
    wire = Wire(quota(), step("POST", "/123/media", {"id": "100"}),
                step("GET", "/100?fields=status_code,status", {"status_code": "IN_PROGRESS"}),
                step("GET", "/100?fields=status_code,status", {"status_code": "FINISHED"}), quota(),
                step("POST", "/123/media_publish", {"id": "200"}),
                step("GET", "/200?fields=permalink", {"permalink": "https://www.instagram.com/p/fixture/"}))
    first = publish.publish_content(desk, row["id"], http=wire)
    assert first["pending"] and not first["confirmed"]
    assert first["content"]["status"] == "publishing" and not first["content"].get("remote_id")
    assert first["content"]["receipt"]["operation_id"] == "100"
    assert "graph.instagram.com/v23.0" in wire.calls[0]["url"]
    assert publish.publish_status(desk, row["id"], http=wire)["pending"]
    assert len(wire.calls) == 3
    final = publish.publish_status(desk, row["id"], http=wire)
    assert final["confirmed"] and final["content"]["remote_id"] == "200"
    assert final["content"]["published_url"] == "https://www.instagram.com/p/fixture/"
    assert parse_qs(wire.calls[5]["body"].decode()) == {"creation_id": ["100"]}
    assert publish.publish_status(desk, row["id"], http=wire)["confirmed"]
    wire.done()


@pytest.mark.parametrize("provider_state", ["ERROR", "EXPIRED"])
def test_instagram_processing_failures_keep_the_error(desk, provider_state):
    row = content(desk, "instagram")
    wire = Wire(quota(), step("POST", "/123/media", {"id": "100"}),
                step("GET", "/100?fields=status_code,status", {"status_code": provider_state, "status": "Unsupported image"}))
    publish.publish_content(desk, row["id"], http=wire)
    result = publish.publish_status(desk, row["id"], http=wire)
    assert result["content"]["status"] == "failed" and not result["pending"]
    assert not result["ok"] and "Unsupported image" in result["error"]
    wire.done()


def test_instagram_quota_blocks_creation_before_a_mutation(desk):
    row = content(desk, "instagram")
    wire = Wire(quota(100))
    result = publish.publish_content(desk, row["id"], http=wire)
    assert not result["ok"] and result["status"] == 429
    assert result["content"]["status"] == "failed"
    wire.done()


def test_instagram_publish_timeout_does_not_repeat_the_mutation(desk):
    row = content(desk, "instagram")
    wire = Wire(quota(), step("POST", "/123/media", {"id": "100"}),
                step("GET", "/100?fields=status_code,status", {"status_code": "FINISHED"}), quota(),
                step("POST", "/123/media_publish", TimeoutError("response lost")),
                step("GET", "/100?fields=status_code,status", {"status_code": "FINISHED"}),
                step("GET", "/100?fields=status_code,status", {"status_code": "PUBLISHED"}))
    publish.publish_content(desk, row["id"], http=wire)
    uncertain = publish.publish_status(desk, row["id"], http=wire)
    assert uncertain["pending"] and not uncertain["confirmed"]
    assert publish.publish_status(desk, row["id"], http=wire)["pending"]
    final = publish.publish_status(desk, row["id"], http=wire)
    assert final["confirmed"] and not final["error"]
    assert final["content"]["remote_id"] == ""
    assert len([call for call in wire.calls if call["method"] == "POST" and call["url"].endswith("/media_publish")]) == 1
    wire.done()


def test_instagram_generated_png_gets_a_real_jpeg_and_no_dry_run_mutation(desk):
    path = image_file(desk, kind="PNG")
    desk.save_settings({"media_base_url": "https://media.example.com"})
    row = content(desk, "instagram", media_url="", media_path=str(path))
    before = tree(desk.root)
    dry = publish.publish_content(desk, row["id"], dry_run=True, http=Wire())
    assert dry["dry_run"] and tree(desk.root) == before
    wire = Wire(quota(), step("POST", "/123/media", {"id": "100"}))
    result = publish.publish_content(desk, row["id"], http=wire)
    assert result["pending"]
    url = parse_qs(wire.calls[1]["body"].decode())["image_url"][0]
    filename = parse_qs(url.split("?", 1)[1])["name"][0]
    assert filename.endswith(".jpg")
    with Image.open(desk.root / "assets" / filename) as image:
        assert image.format == "JPEG"
    assert path.read_bytes() == before[str(path.relative_to(desk.root))]
    wire.done()


@pytest.mark.parametrize("change", [{"publish_consent": False}, {"music_usage_confirmed": False}, {"privacy_level": ""}, {"brand_content_toggle": True, "branded_content_policy_confirmed": False}])
def test_tiktok_never_invents_required_choices(desk, change):
    row = content(desk, "tiktok", **change)
    wire = Wire()
    before = tree(desk.root)
    with pytest.raises(MarketingError):
        publish.publish_content(desk, row["id"], http=wire)
    assert not wire.calls
    assert desk.get_content(row["id"])["status"] == "approved"
    # The publication lock may be created; content and OAuth files are unchanged.
    after = tree(desk.root)
    assert all(after[name] == value for name, value in before.items())


def test_tiktok_checks_current_privacy_before_init(desk):
    row = content(desk, "tiktok", privacy_level="PUBLIC_TO_EVERYONE")
    wire = Wire(creator(privacy_level_options=["SELF_ONLY"]))
    result = publish.publish_content(desk, row["id"], http=wire)
    assert not result["ok"] and result["content"]["status"] == "failed"
    assert result["content"]["receipt"]["error_code"] == "privacy_level_option_mismatch"
    wire.done()


def test_tiktok_upload_then_private_completion_has_no_invented_public_url(desk, monkeypatch):
    path = video_file(desk, monkeypatch)
    row = content(desk, "tiktok", media_url="", media_path=str(path), disable_comment=False, disable_duet=False, disable_stitch=False)
    wire = Wire(creator(comment_disabled=True, duet_disabled=True),
                step("POST", "/video/init/", {"data": {"publish_id": "v_pub_fixture", "upload_url": "https://open-upload.tiktokapis.com/video/?upload_id=fixture"}, "error": {"code": "ok"}}),
                step("PUT", "https://open-upload.tiktokapis.com/video/", b"", 201),
                step("POST", "/status/fetch/", {"data": {"status": "PROCESSING_UPLOAD"}, "error": {"code": "ok"}}),
                step("POST", "/status/fetch/", {"data": {"status": "PUBLISH_COMPLETE"}, "error": {"code": "ok"}}))
    result = publish.publish_content(desk, row["id"], http=wire)
    assert result["pending"] and not result["confirmed"]
    payload = json.loads(wire.calls[1]["body"])
    assert payload["source_info"] == {"source": "FILE_UPLOAD", "video_size": 64, "chunk_size": 64, "total_chunk_count": 1}
    assert payload["post_info"]["privacy_level"] == "SELF_ONLY"
    assert payload["post_info"]["disable_comment"] and payload["post_info"]["disable_duet"]
    assert payload["post_info"]["disable_stitch"] is False
    assert wire.calls[2]["headers"]["Content-Range"] == "bytes 0-63/64"
    assert wire.calls[2]["body"] == path.read_bytes()
    assert publish.publish_status(desk, row["id"], http=wire)["pending"]
    final = publish.publish_status(desk, row["id"], http=wire)
    assert final["confirmed"] and final["content"]["status"] == "published"
    assert final["content"]["remote_id"] == final["content"]["published_url"] == ""
    wire.done()


def test_tiktok_chunk_ranges_include_the_final_remainder(desk, monkeypatch):
    size = 17 * 1024 * 1024 + 3
    path = video_file(desk, monkeypatch, size=size)
    row = content(desk, "tiktok", media_path=str(path), media_url="")
    wire = Wire(creator(), step("POST", "/video/init/", {"data": {"publish_id": "fixture", "upload_url": "https://upload.us.tiktokapis.com/video/"}, "error": {"code": "ok"}}),
                step("PUT", "https://upload.us.tiktokapis.com/video/", b"", 206), step("PUT", "https://upload.us.tiktokapis.com/video/", b"", 201))
    result = publish.publish_content(desk, row["id"], http=wire)
    assert result["pending"]
    assert json.loads(wire.calls[1]["body"])["source_info"]["total_chunk_count"] == 2
    assert wire.calls[2]["headers"]["Content-Range"] == f"bytes 0-8388607/{size}"
    assert wire.calls[3]["headers"]["Content-Range"] == f"bytes 8388608-{size - 1}/{size}"
    assert sum(len(call["body"]) for call in wire.calls if call["method"] == "PUT") == size
    wire.done()


def test_tiktok_server_hosted_video_uses_the_url_even_with_a_local_copy(desk, monkeypatch):
    path = video_file(desk, monkeypatch)
    row = content(desk, "tiktok", media_path=str(path))
    wire = Wire(creator(), step("POST", "/video/init/", {"data": {"publish_id": "fixture"}, "error": {"code": "ok"}}))
    assert publish.publish_content(desk, row["id"], http=wire)["pending"]
    assert json.loads(wire.calls[1]["body"])["source_info"] == {"source": "PULL_FROM_URL", "video_url": row["media_url"]}
    wire.done()


def test_tiktok_photo_mode_and_async_failure(desk):
    row = content(desk, "tiktok", media_type="image", media_url="", photo_images=["https://media.example.com/a.jpg", "https://media.example.com/b.webp"], photo_cover_index=1)
    wire = Wire(creator(), step("POST", "/content/init/", {"data": {"publish_id": "photo_fixture"}, "error": {"code": "ok"}}),
                step("POST", "/status/fetch/", {"data": {"status": "FAILED", "fail_reason": "url_ownership_unverified"}, "error": {"code": "ok"}}))
    assert publish.publish_content(desk, row["id"], http=wire)["pending"]
    payload = json.loads(wire.calls[1]["body"])
    assert payload["post_mode"] == "DIRECT_POST" and payload["media_type"] == "PHOTO"
    assert payload["source_info"]["photo_cover_index"] == 1
    assert payload["post_info"]["disable_comment"] is True
    failed = publish.publish_status(desk, row["id"], http=wire)
    assert failed["content"]["status"] == "failed" and not failed["pending"]
    assert failed["error"] == "url_ownership_unverified"
    wire.done()


def test_tiktok_quota_error_inside_http_200_is_not_a_publish_id(desk):
    row = content(desk, "tiktok")
    wire = Wire(creator(), step("POST", "/video/init/", ({"Retry-After": "60"}, {"error": {"code": "spam_risk_too_many_posts", "message": "Daily cap reached"}})))
    result = publish.publish_content(desk, row["id"], http=wire)
    assert not result["pending"] and not result["confirmed"] and result["status"] == 429
    assert result["content"]["receipt"]["retry_after_s"] == 60
    wire.done()


def test_linkedin_image_upload_waits_for_media_before_creating_a_post(desk):
    path = image_file(desk)
    row = content(desk, "linkedin", media_type="image", media_path=str(path))
    wire = Wire(step("POST", "/rest/images?action=initializeUpload", {"value": {"image": "urn:li:image:fixture", "uploadUrl": "https://www.linkedin.com/dms-uploads/fixture"}}),
                step("PUT", "/dms-uploads/fixture", b"", 201),
                step("GET", "/rest/images/urn%3Ali%3Aimage%3Afixture", {"status": "PROCESSING"}),
                step("GET", "/rest/images/urn%3Ali%3Aimage%3Afixture", {"status": "AVAILABLE"}),
                step("POST", "/rest/posts", ({"x-restli-id": "urn:li:share:fixture"}, b""), 201))
    first = publish.publish_content(desk, row["id"], http=wire)
    assert first["pending"] and len(wire.calls) == 2
    assert wire.calls[1]["body"] == path.read_bytes()
    assert publish.publish_status(desk, row["id"], http=wire)["pending"]
    assert not any(call["url"].endswith("/rest/posts") for call in wire.calls)
    final = publish.publish_status(desk, row["id"], http=wire)
    assert final["confirmed"] and final["content"]["remote_id"] == "urn:li:share:fixture"
    payload = json.loads(wire.calls[-1]["body"])
    assert payload["content"]["media"]["id"] == "urn:li:image:fixture"
    assert "article" not in payload["content"]
    assert wire.calls[-1]["headers"]["LinkedIn-Version"] == "202607"
    wire.done()


def test_linkedin_video_uploads_ranges_and_finalizes_etags(desk, monkeypatch):
    path = video_file(desk, monkeypatch, size=10)
    path.write_bytes(b"abcdefghij")
    row = content(desk, "linkedin", media_type="video", media_path=str(path))
    wire = Wire(step("POST", "/rest/videos?action=initializeUpload", {"value": {
        "video": "urn:li:video:fixture", "uploadToken": "private-upload-token",
        "uploadInstructions": [{"firstByte": 0, "lastByte": 4, "uploadUrl": "https://www.linkedin.com/dms-uploads/part1"}, {"firstByte": 5, "lastByte": 9, "uploadUrl": "https://www.linkedin.com/dms-uploads/part2"}],
    }}), step("PUT", "/dms-uploads/part1", ({"ETag": "part-1"}, b"")), step("PUT", "/dms-uploads/part2", ({"ETag": "part-2"}, b"")),
                step("POST", "/rest/videos?action=finalizeUpload", b"", 204))
    result = publish.publish_content(desk, row["id"], http=wire)
    assert result["pending"] and not result["confirmed"]
    assert [wire.calls[i]["body"] for i in [1, 2]] == [b"abcde", b"fghij"]
    assert json.loads(wire.calls[-1]["body"])["finalizeUploadRequest"]["uploadedPartIds"] == ["part-1", "part-2"]
    assert "private-upload-token" not in json.dumps(result)
    wire.done()


def test_facebook_photo_uses_binary_photo_endpoint(desk):
    path = image_file(desk)
    row = content(desk, "facebook", media_type="image", media_path=str(path))
    wire = Wire(step("POST", "/456/photos", {"id": "photo-7", "post_id": "456_7"}))
    result = publish.publish_content(desk, row["id"], http=wire)
    assert result["confirmed"] and result["content"]["remote_id"] == "456_7"
    assert b'name="source"' in wire.calls[0]["body"] and path.read_bytes() in wire.calls[0]["body"]
    wire.done()


def test_facebook_reel_only_reports_success_after_publishing_phase(desk, monkeypatch):
    path = video_file(desk, monkeypatch)
    row = content(desk, "facebook", media_type="video", media_path=str(path), duration_s=12)
    wire = Wire(step("POST", "/456/video_reels", {"video_id": "900", "upload_url": "https://rupload.facebook.com/video-upload/v23.0/900"}),
                step("POST", "https://rupload.facebook.com/video-upload/", {"success": True}),
                step("POST", "/456/video_reels", {"success": True}),
                step("GET", "/900?fields=status,permalink_url", {"status": {"video_status": "ready", "processing_phase": {"status": "complete"}, "publishing_phase": {"status": "in_progress"}}}),
                step("GET", "/900?fields=status,permalink_url", {"status": {"publishing_phase": {"status": "complete"}}, "permalink_url": "https://www.facebook.com/reel/900"}))
    result = publish.publish_content(desk, row["id"], http=wire)
    assert result["pending"] and not result["confirmed"]
    assert wire.calls[1]["body"] == path.read_bytes()
    assert parse_qs(wire.calls[2]["body"].decode())["upload_phase"] == ["finish"]
    assert publish.publish_status(desk, row["id"], http=wire)["pending"]
    final = publish.publish_status(desk, row["id"], http=wire)
    assert final["confirmed"] and final["content"]["published_url"] == "https://www.facebook.com/reel/900"
    wire.done()


@pytest.mark.parametrize("channel", ["linkedin", "facebook"])
def test_missing_text_post_receipt_is_uncertain_and_cannot_be_retried_blindly(desk, channel):
    row = content(desk, channel)
    wire = Wire(step("POST", "/rest/posts" if channel == "linkedin" else "/456/feed", {}, 201 if channel == "linkedin" else 200))
    result = publish.publish_content(desk, row["id"], http=wire)
    assert result["pending"] and not result["confirmed"] and not result["ok"]
    assert result["content"]["status"] == "publishing"
    with pytest.raises(MarketingError, match="already publishing"):
        publish.publish_content(desk, row["id"], http=wire, force=True)
    wire.done()


@pytest.mark.parametrize("channel,fields", [
    ("instagram", {"media_url": "http://localhost/photo.jpg"}),
    ("instagram", {"media_url": "https://media.example.com/photo.png"}),
    ("instagram", {"media_type": "video", "duration_s": 901, "media_url": "https://media.example.com/video.mp4"}),
    ("tiktok", {"media_url": "https://127.0.0.1/video.mp4"}),
    ("tiktok", {"media_type": "image", "photo_images": ["https://media.example.com/a.jpg"] * 36}),
    ("linkedin", {"media_type": "image", "media_url": "https://media.example.com/a.jpg"}),
    ("facebook", {"media_type": "video", "media_url": "https://media.example.com/a.mp4", "duration_s": 100}),
    ("reddit", {"title": "x" * 301}),
])
def test_invalid_media_is_refused_without_network_or_false_preview(desk, channel, fields):
    row = content(desk, channel, **fields)
    wire = Wire()
    capabilities = publish.content_capabilities(desk, row)
    assert not capabilities["can_publish"] and capabilities["errors"]
    with pytest.raises(MarketingError):
        publish.publish_content(desk, row["id"], http=wire)
    assert not wire.calls and desk.get_content(row["id"])["status"] == "approved"


def test_token_refresh_is_injected_and_stable_identity_allows_continuation(desk):
    now = time.time()
    _atomic_write(desk.path("oauth.json"), {"instagram": {"account_id": "123", "connected_at": now - 172800, "expires_at": now + 120}})
    row = content(desk, "instagram")
    wire = Wire(step("GET", "/refresh_access_token?", {"access_token": "refreshed-on-start", "expires_in": 3600}), quota(), step("POST", "/123/media", {"id": "100"}),
                step("GET", "/refresh_access_token?", {"access_token": "refreshed-on-status", "expires_in": 3600}),
                step("GET", "/100?fields=status_code,status", {"status_code": "FINISHED"}), quota(), step("POST", "/123/media_publish", {"id": "200"}),
                step("GET", "/200?fields=permalink", {"permalink": "https://www.instagram.com/p/fixture/"}))
    initial = publish.publish_content(desk, row["id"], http=wire)
    assert initial["pending"] and "manual_token_fingerprint" not in initial["content"]["receipt"]["account_binding"]
    assert wire.calls[2]["headers"]["Authorization"] == "Bearer refreshed-on-start"
    _atomic_write(desk.path("oauth.json"), {"instagram": {"account_id": "123", "connected_at": now - 172800, "expires_at": now + 120}})
    final = publish.publish_status(desk, row["id"], http=wire)
    assert final["confirmed"] and wire.calls[6]["headers"]["Authorization"] == "Bearer refreshed-on-status"
    assert "refreshed-on-" not in json.dumps(final)
    wire.done()


def test_expiring_oauth_dry_run_does_not_refresh_or_create_a_lock(desk):
    now = time.time()
    _atomic_write(desk.path("oauth.json"), {"instagram": {"account_id": "123", "connected_at": now - 172800, "expires_at": now + 120}})
    row = content(desk, "instagram")
    before = tree(desk.root)
    result = publish.publish_content(desk, row["id"], http=Wire(), dry_run=True)
    assert result["dry_run"] and tree(desk.root) == before


def test_account_switch_or_pause_cannot_finalize_an_existing_container(desk):
    row = content(desk, "instagram")
    wire = Wire(quota(), step("POST", "/123/media", {"id": "100"}))
    publish.publish_content(desk, row["id"], http=wire)
    desk.save_settings({"publish": {"instagram": {"enabled": False}}})
    paused = publish.publish_status(desk, row["id"], http=wire)
    assert paused["pending"] and "paused" in paused["error"]
    desk.save_settings({"publish": {"instagram": {"enabled": True, "instagram_user_id": "999"}}})
    wrong = publish.publish_status(desk, row["id"], http=wire)
    assert wrong["pending"] and "different account" in wrong["error"]
    assert wrong["content"]["receipt"]["account_binding"]["configured_account"] == "123"
    wire.done()


def test_publish_due_separates_pending_and_polling_respects_delay(desk):
    row = content(desk, "instagram")
    wire = Wire(quota(), step("POST", "/123/media", {"id": "100"}),
                step("GET", "/100?fields=status_code,status", {"status_code": "IN_PROGRESS"}))
    report = publish.publish_due(desk, http=wire)
    assert not report["sent"] and not report["failed"]
    assert report["pending"][0]["id"] == row["id"]
    now = desk.get_content(row["id"])["publish_checked_at"]
    assert publish.poll_pending_publications(desk, http=wire, now=now + 1)["checked"] == 0
    assert publish.poll_pending_publications(desk, http=wire, now=now + 16)["checked"] == 1
    assert len(wire.calls) == 3
    wire.done()


def test_loop_continues_media_before_the_next_daily_cycle_and_respects_pause(desk, monkeypatch):
    row = content(desk, "instagram")
    wire = Wire(quota(), step("POST", "/123/media", {"id": "100"}),
                step("GET", "/100?fields=status_code,status", {"status_code": "FINISHED"}), quota(),
                step("POST", "/123/media_publish", {"id": "200"}), step("GET", "/200?fields=permalink", {}))
    publish.publish_content(desk, row["id"], http=wire)
    now = desk.get_content(row["id"])["publish_checked_at"] + 30
    desk.save_loop({"enabled": True, "phase": "idle", "next_due": now + 86400})
    monkeypatch.setattr(loop, "brand_is_armed", lambda *_: True)
    monkeypatch.setattr(publish, "http_request", wire)
    result = loop.maybe_tick(desk, now=now)
    assert result["did_work"] and result["phase"] == "publishing"
    assert result["publish"]["sent"][0]["confirmed"]
    assert desk.load_loop()["next_due"] == now + 86400
    desk.save_loop({"enabled": False, "phase": "paused", "next_due": now + 86400})
    assert loop.maybe_tick(desk, now=now + 60)["phase"] == "paused"
    wire.done()
