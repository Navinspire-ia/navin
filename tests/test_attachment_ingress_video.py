"""End-to-end shape of an inbound video attachment.

Frame extraction recognizes a video by file extension, so the whole feature
depends on the ingress writing a correctly named file. These tests pin that
contract from the data URL down to the path the agent receives.
"""

from __future__ import annotations

import base64
from pathlib import Path

from navin.utils.media_decode import save_base64_data_url
from navin.utils.video_frames import is_video_path
from navin.webui.attachment_ingress import extract_data_url_mime, store_inbound_attachments


class _Logger:
    def __init__(self) -> None:
        self.messages: list[str] = []

    def warning(self, template: str, *args: object) -> None:
        self.messages.append(template.format(*args) if args else template)


def _data_url(mime: str, payload: bytes = b"\x00\x00\x00\x18ftypmp42rest") -> str:
    return f"data:{mime};base64," + base64.b64encode(payload).decode()


class TestSaveBase64DataUrl:
    def test_video_mimes_get_their_canonical_extension(self, tmp_path: Path):
        cases = {
            "video/mp4": ".mp4",
            "video/quicktime": ".mov",
            "video/webm": ".webm",
        }
        for mime, expected in cases.items():
            saved = save_base64_data_url(_data_url(mime), tmp_path)
            assert saved is not None, mime
            assert Path(saved).suffix == expected, mime

    def test_saved_videos_are_recognized_as_videos(self, tmp_path: Path):
        for mime in ("video/mp4", "video/quicktime", "video/webm"):
            saved = save_base64_data_url(_data_url(mime), tmp_path)
            assert saved is not None
            assert is_video_path(saved), f"{mime} -> {saved}"

    def test_images_keep_working(self, tmp_path: Path):
        saved = save_base64_data_url(_data_url("image/png", b"\x89PNG\r\n\x1a\n"), tmp_path)
        assert saved is not None
        assert Path(saved).suffix == ".png"
        assert not is_video_path(saved)


class TestStoreInboundAttachments:
    def test_a_video_survives_ingestion_as_an_analyzable_path(self, tmp_path: Path):
        paths, rejection = store_inbound_attachments(
            [{"data_url": _data_url("video/mp4")}],
            media_dir=tmp_path,
            logger=_Logger(),
        )
        assert rejection is None
        assert len(paths) == 1
        assert is_video_path(paths[0])
        assert Path(paths[0]).is_file()

    def test_a_video_alongside_images_keeps_both(self, tmp_path: Path):
        paths, rejection = store_inbound_attachments(
            [
                {"data_url": _data_url("image/png", b"\x89PNG\r\n\x1a\n")},
                {"data_url": _data_url("video/mp4")},
            ],
            media_dir=tmp_path,
            logger=_Logger(),
        )
        assert rejection is None
        assert len(paths) == 2
        assert sum(is_video_path(p) for p in paths) == 1

    def test_more_than_one_video_is_rejected(self, tmp_path: Path):
        paths, rejection = store_inbound_attachments(
            [{"data_url": _data_url("video/mp4")}, {"data_url": _data_url("video/webm")}],
            media_dir=tmp_path,
            logger=_Logger(),
        )
        assert rejection == "too_many_videos"
        assert paths == []

    def test_an_unlisted_mime_is_rejected_without_leaving_files(self, tmp_path: Path):
        paths, rejection = store_inbound_attachments(
            [{"data_url": _data_url("video/x-msvideo")}],
            media_dir=tmp_path,
            logger=_Logger(),
        )
        assert rejection == "mime"
        assert paths == []
        assert list(tmp_path.iterdir()) == []

    def test_a_rejected_batch_cleans_up_earlier_files(self, tmp_path: Path):
        paths, rejection = store_inbound_attachments(
            [
                {"data_url": _data_url("image/png", b"\x89PNG\r\n\x1a\n")},
                {"data_url": _data_url("application/x-msdownload")},
            ],
            media_dir=tmp_path,
            logger=_Logger(),
        )
        assert rejection == "mime"
        assert paths == []
        assert list(tmp_path.iterdir()) == []


class TestExtractDataUrlMime:
    def test_reads_the_mime(self):
        assert extract_data_url_mime(_data_url("video/mp4")) == "video/mp4"

    def test_rejects_non_data_urls(self):
        assert extract_data_url_mime("https://example.com/a.mp4") is None
        assert extract_data_url_mime(None) is None
