# Copyright (c) 2026-present Navinspire IA
# SPDX-License-Identifier: AGPL-3.0-only

"""Pasted media links, and telling the truth when a model cannot see an image.

Four behaviours are pinned here, all of which used to fail silently:
a pasted YouTube link produced nothing, a pasted image link depended on the
model deciding to fetch it, a text-only model received images and answered as
if it had seen them, and Chromium had no install path other than a sentence in
an error message.
"""

from __future__ import annotations

import asyncio
from pathlib import Path
from unittest.mock import patch

import pytest

from navin.agent.model_routes import resolve_vision_route, text_needs_vision
from navin.agent.vision_guard import guard_vision_media
from navin.utils import media_urls
from navin.utils.media_urls import (
    FetchedMedia,
    MediaLink,
    classify_url,
    describe_fetch,
    expand_media_urls,
    find_media_links,
)


class TestVisionGuard:
    def test_vision_model_keeps_its_images(self):
        text, media, withheld = guard_vision_media("look", ["/tmp/a.png"], "gpt-4o")
        assert media == ["/tmp/a.png"]
        assert withheld is False
        assert text == "look"

    def test_text_only_model_loses_them_and_is_told_so(self):
        text, media, withheld = guard_vision_media(
            "look", ["/tmp/shot.png"], "deepseek-chat"
        )
        assert media == []
        assert withheld is True
        assert "shot.png" in text
        assert "deepseek-chat" in text
        # The model must not fill the gap with an invention.
        assert "Do not describe or guess" in text
        assert "look" in text

    def test_many_images_are_summarized_not_listed_forever(self):
        paths = [f"/tmp/frame-{index}.jpg" for index in range(9)]
        text, media, withheld = guard_vision_media("", paths, "deepseek-chat")
        assert media == []
        assert withheld is True
        assert "9 images" in text
        assert "+5 more" in text

    def test_no_media_is_untouched(self):
        assert guard_vision_media("hi", [], "deepseek-chat") == ("hi", [], False)

    def test_declared_modalities_win_over_the_name(self):
        text, media, withheld = guard_vision_media(
            "look", ["/tmp/a.png"], "some-private-model", input_modalities=["text", "image"]
        )
        assert media == ["/tmp/a.png"]
        assert withheld is False

    def test_unknown_model_is_treated_as_text_only(self):
        """Withholding with an explanation beats a silent drop by the gateway."""
        _text, media, withheld = guard_vision_media("look", ["/tmp/a.png"], "mystery-7b")
        assert media == []
        assert withheld is True

    @pytest.mark.parametrize(
        "model", ["gpt-4o", "claude-sonnet-4", "gemini-2.5-pro", "grok-4", "pixtral-12b"]
    )
    def test_known_vision_families_pass(self, model: str):
        _text, media, withheld = guard_vision_media("x", ["/tmp/a.png"], model)
        assert media == ["/tmp/a.png"], model
        assert withheld is False


class TestClassifyURL:
    @pytest.mark.parametrize(
        "url",
        [
            "https://example.com/shot.png",
            "https://example.com/a/b/photo.JPEG",
            "https://cdn.example.com/x.webp?sig=abc",
        ],
    )
    def test_image_urls(self, url: str):
        assert classify_url(url) == "image"

    @pytest.mark.parametrize(
        "url", ["https://example.com/clip.mp4", "https://example.com/rec.webm"]
    )
    def test_direct_video_urls(self, url: str):
        assert classify_url(url) == "video"

    @pytest.mark.parametrize(
        "url",
        [
            "https://www.youtube.com/watch?v=dQw4w9WgXcQ",
            "https://youtu.be/dQw4w9WgXcQ",
            "https://vimeo.com/123456",
            "https://www.loom.com/share/abc123",
            "https://clips.twitch.tv/SomeClip",
        ],
    )
    def test_video_sites(self, url: str):
        assert classify_url(url) == "video_site"

    @pytest.mark.parametrize(
        "url",
        [
            "https://example.com/docs/page",
            "https://github.com/org/repo",
            "https://example.com/report.pdf",
            "ftp://example.com/clip.mp4",
            "not a url",
        ],
    )
    def test_plain_links_are_left_alone(self, url: str):
        assert classify_url(url) is None


class TestFindMediaLinks:
    def test_extracts_from_prose(self):
        links = find_media_links("regarde https://x.com/a.png stp")
        assert [link.url for link in links] == ["https://x.com/a.png"]

    def test_strips_trailing_punctuation(self):
        links = find_media_links("voici (https://x.com/a.png).")
        assert links[0].url == "https://x.com/a.png"

    def test_keeps_a_balanced_closing_paren(self):
        links = find_media_links("https://x.com/a_(1).png")
        assert links[0].url == "https://x.com/a_(1).png"

    def test_deduplicates(self):
        links = find_media_links("https://x.com/a.png et https://x.com/a.png")
        assert len(links) == 1

    def test_is_capped(self):
        text = " ".join(f"https://x.com/{i}.png" for i in range(10))
        assert len(find_media_links(text)) == media_urls.MAX_URLS_PER_MESSAGE

    def test_ignores_non_media(self):
        assert find_media_links("https://news.ycombinator.com/item?id=1") == []

    def test_empty_text(self):
        assert find_media_links("") == []


class TestVisionRouting:
    def test_a_pasted_youtube_link_asks_for_a_vision_model(self):
        assert text_needs_vision("regarde https://youtu.be/abc") is True

    def test_a_plain_link_does_not(self):
        assert text_needs_vision("regarde https://example.com/docs") is False

    def test_text_without_url_is_cheap(self):
        assert text_needs_vision("aucun lien ici") is False

    def test_route_triggers_on_text_alone(self):
        """A link-only message carries no media yet, so media alone is not enough."""
        route = resolve_vision_route(
            [], text="https://youtu.be/abc", routes={"vision": "gpt-4o"},
            known_presets={"gpt-4o"},
        )
        assert route == "gpt-4o"

    def test_no_route_without_media_or_link(self):
        assert resolve_vision_route([], text="bonjour", routes={"vision": "gpt-4o"}) is None


class TestDescribeFetch:
    def test_failure_forbids_invention(self):
        note = describe_fetch(
            FetchedMedia("https://x/a.png", "image", reason="HTTP 404")
        )
        assert "HTTP 404" in note
        assert "Do not describe or guess" in note

    def test_success_is_announced(self):
        note = describe_fetch(FetchedMedia("https://x/a.png", "image", path="/tmp/a.png"))
        assert "https://x/a.png" in note


class TestExpandMediaURLs:
    def test_no_links_is_a_no_op(self):
        result = asyncio.run(expand_media_urls("bonjour", ["/tmp/a.png"]))
        assert result.text == "bonjour"
        assert result.media == ["/tmp/a.png"]
        assert result.results == []

    def test_downloaded_file_is_appended_and_announced(self):
        async def fake_fetch(link: MediaLink) -> FetchedMedia:
            return FetchedMedia(link.url, link.kind, path="/tmp/downloaded.png")

        with patch.object(media_urls, "fetch_media_link", side_effect=fake_fetch):
            result = asyncio.run(expand_media_urls("vois https://x.com/a.png", []))
        assert result.media == ["/tmp/downloaded.png"]
        assert "https://x.com/a.png" in result.text
        assert "vois" in result.text

    def test_failure_leaves_media_empty_but_explains(self):
        async def fake_fetch(link: MediaLink) -> FetchedMedia:
            return FetchedMedia(link.url, link.kind, reason="HTTP 403")

        with patch.object(media_urls, "fetch_media_link", side_effect=fake_fetch):
            result = asyncio.run(expand_media_urls("vois https://x.com/a.png", []))
        assert result.media == []
        assert "HTTP 403" in result.text

    def test_existing_attachments_are_preserved_and_ordered_first(self):
        async def fake_fetch(link: MediaLink) -> FetchedMedia:
            return FetchedMedia(link.url, link.kind, path="/tmp/new.png")

        with patch.object(media_urls, "fetch_media_link", side_effect=fake_fetch):
            result = asyncio.run(
                expand_media_urls("https://x.com/a.png", ["/tmp/original.png"])
            )
        assert result.media == ["/tmp/original.png", "/tmp/new.png"]


class TestDirectDownloadGuards:
    def _link(self, kind: str = "image") -> MediaLink:
        return MediaLink(url="https://example.com/a.png", kind=kind)

    def test_ssrf_rejection_is_reported_not_raised(self):
        with patch(
            "navin.security.network.resolve_url_target",
            return_value=(False, "private address", ()),
        ):
            result = asyncio.run(media_urls._download_direct(self._link()))
        assert not result.ok
        assert "blocked" in (result.reason or "")

    def test_video_site_without_yt_dlp_says_so(self):
        with patch.dict("sys.modules", {"yt_dlp": None}):
            result = media_urls._download_video_site_sync("https://youtu.be/abc")
        assert not result.ok
        assert "yt-dlp" in (result.reason or "")

    def test_caps_are_sane(self):
        assert media_urls.MAX_IMAGE_BYTES < media_urls.MAX_VIDEO_BYTES
        assert media_urls.MAX_URLS_PER_MESSAGE <= 5
        assert media_urls.VIDEO_SITE_MAX_HEIGHT <= 720

    def test_cache_lives_outside_the_workspace(self):
        root = media_urls.cache_root()
        assert ".navin" in root.parts
        assert Path.cwd() not in root.parents


class TestChromiumPackage:
    def test_it_is_in_the_catalog(self):
        from navin.montage.packages import get_package

        package = get_package("chromium")
        assert package is not None
        assert package.tier == "system"
        assert package.size_mb > 0

    def test_an_existing_browser_is_reused_not_redownloaded(self):
        from navin.montage import install

        with patch.object(install, "_run_argv") as run_argv:
            with patch(
                "navin.agent.tools.browser._installed_chromium",
                return_value="/usr/bin/google-chrome",
            ):
                result = asyncio.run(install.install_chromium())
        assert result["ok"] is True
        assert result["path"] == "/usr/bin/google-chrome"
        run_argv.assert_not_called()

    def test_install_uses_the_bundled_interpreter_indirection(self):
        from navin.montage.install import _chromium_install_argv

        argv = _chromium_install_argv()
        assert argv[-3:] == ["playwright", "install", "chromium"]
        assert "-m" in argv

    def test_failure_mentions_the_linux_system_libraries_trap(self):
        from navin.montage import install

        class _Proc:
            returncode = 1
            stdout = ""
            stderr = "host system is missing dependencies"

        with patch.object(install, "_run_argv", return_value=_Proc()):
            with patch(
                "navin.agent.tools.browser._installed_chromium", return_value=None
            ):
                result = asyncio.run(install.install_chromium())
        assert result["ok"] is False
        assert "--with-deps" in result["fix"]
