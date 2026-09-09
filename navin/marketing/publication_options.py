"""Edit a draft and its media without changing a publication already in flight."""

from __future__ import annotations

import math
from typing import Any

from navin.marketing.errors import MarketingError
from navin.marketing.store import MarketingStore
from navin.utils.atomic_io import InterProcessLock, LockTimeoutError

_TEXT = {"title", "body", "creative_id", "media_url", "media_path", "media_type", "reddit_kind", "reddit_subreddit", "reddit_url", "reddit_flair_id", "privacy_level"}
_BOOL = {"disable_comment", "disable_duet", "disable_stitch", "brand_content_toggle", "brand_organic_toggle", "is_aigc", "auto_add_music", "publish_consent", "music_usage_confirmed", "branded_content_policy_confirmed"}
_CONSENT = {"publish_consent", "music_usage_confirmed", "branded_content_policy_confirmed"}
_EDITABLE = _TEXT | _BOOL | {"duration_s", "photo_images", "photo_cover_index"}


def update_publication_options(store: MarketingStore, content_id: str, fields: dict[str, Any]) -> dict[str, Any]:
    try:
        with InterProcessLock(store.path(".publish.lock"), timeout=0):
            current = store.get_content(content_id)
            if current.get("status") in {"publishing", "published", "winner", "retired"}:
                raise MarketingError("Create a new draft to change content that is already publishing or published", status=409)
            if fields.get("revision") != current.get("updated_at"):
                raise MarketingError("This draft changed; refresh it before saving your publication choices", status=409)
            patch = {key: value for key, value in fields.items() if key in _EDITABLE}
            for key in _TEXT & patch.keys():
                if not isinstance(patch[key], str) or len(patch[key]) > (50000 if key == "body" else 2048):
                    raise MarketingError(f"Invalid publication field: {key}", status=400)
                patch[key] = patch[key].strip()
            for key in _BOOL & patch.keys():
                if not isinstance(patch[key], bool):
                    raise MarketingError(f"{key} must be a boolean", status=400)
            if patch.get("media_type") not in {None, "", "image", "video"}:
                raise MarketingError("Select image or video", status=400)
            if patch.get("reddit_kind") not in {None, "", "self", "link"}:
                raise MarketingError("Reddit posts must be text or a link", status=400)
            if patch.get("creative_id") and not any(item.get("id") == patch["creative_id"] for item in store.load_creatives()):
                raise MarketingError("Selected studio media was not found", status=404)
            if "duration_s" in patch:
                try:
                    duration = float(patch["duration_s"])
                except (TypeError, ValueError):
                    raise MarketingError("Video duration must be a number", status=400) from None
                if not math.isfinite(duration) or duration < 0:
                    raise MarketingError("Video duration must be positive", status=400)
                patch["duration_s"] = duration
            if "photo_images" in patch and (not isinstance(patch["photo_images"], list) or len(patch["photo_images"]) > 35 or not all(isinstance(url, str) and url.startswith("https://") and len(url) < 2048 for url in patch["photo_images"])):
                raise MarketingError("Photo posts accept up to 35 public HTTPS image URLs", status=400)
            if "photo_cover_index" in patch and (type(patch["photo_cover_index"]) is not int or not 0 <= patch["photo_cover_index"] < 35):
                raise MarketingError("Invalid cover photo index", status=400)
            if any(current.get(key) != value for key, value in patch.items() if key not in _CONSENT):
                # A previous consent does not cover newly edited copy or media.
                patch = {**{key: False for key in _CONSENT}, **patch}
            return store.upsert_content({**patch, "id": content_id, "status": "draft", "approved_at": 0, "scheduled_at": 0, "error": ""})
    except LockTimeoutError:
        raise MarketingError("A publication is in progress; wait before editing this draft", status=409) from None
