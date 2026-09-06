"""End-to-end shape of an inbound audio attachment.

Transcription recognizes an audio attachment by file extension, so the whole
feature depends on the ingress writing a correctly named file. These tests pin
that contract from the data URL down to the path the agent receives.
"""

from __future__ import annotations

import base64
from pathlib import Path

from navin.utils.audio_transcripts import is_audio_path
from navin.utils.media_decode import save_base64_data_url
from navin.utils.video_frames import is_video_path
from navin.webui import attachment_ingress
from navin.webui.attachment_ingress import store_inbound_attachments
from navin.webui.ingress_policy import AttachmentIngressLimits


class _Logger:
    def __init__(self) -> None:
        self.messages: list[str] = []

    def warning(self, template: str, *args: object) -> None:
        self.messages.append(template.format(*args) if args else template)


def _data_url(mime: str, payload: bytes = b"ID3\x04\x00\x00\x00\x00\x00\x00rest") -> str:
    return f"data:{mime};base64," + base64.b64encode(payload).decode()


class TestSaveBase64DataUrl:
    def test_audio_mimes_get_an_extension_the_transcriber_recognizes(self, tmp_path: Path):
        for mime in sorted(attachment_ingress._AUDIO_MIME_ALLOWED):
            saved = save_base64_data_url(_data_url(mime), tmp_path)
            assert saved is not None, mime
            assert is_audio_path(saved), f"{mime} -> {saved}"
            assert not is_video_path(saved), f"{mime} -> {saved}"

    def test_the_original_name_is_kept_for_the_model_note(self, tmp_path: Path):
        saved = save_base64_data_url(_data_url("audio/mpeg"), tmp_path, filename="standup-notes.mp3")
        assert saved is not None
        assert Path(saved).name.endswith("_standup-notes.mp3")


class TestStoreInboundAttachments:
    def test_an_audio_file_survives_ingestion_as_a_transcribable_path(self, tmp_path: Path):
        paths, rejection = store_inbound_attachments(
            [{"data_url": _data_url("audio/mpeg"), "name": "memo.mp3"}],
            media_dir=tmp_path,
            logger=_Logger(),
        )
        assert rejection is None
        assert len(paths) == 1
        assert is_audio_path(paths[0])
        assert Path(paths[0]).name.endswith("_memo.mp3")

    def test_audio_counts_toward_the_attachment_cap(self, tmp_path: Path):
        limits = AttachmentIngressLimits(max_count=2)
        paths, rejection = store_inbound_attachments(
            [
                {"data_url": _data_url("audio/mpeg")},
                {"data_url": _data_url("audio/wav")},
                {"data_url": _data_url("application/pdf", b"%PDF-1.4 rest")},
            ],
            media_dir=tmp_path,
            logger=_Logger(),
            limits=limits,
        )
        assert rejection == "too_many_attachments"
        assert paths == []

    def test_audio_has_its_own_size_ceiling(self, tmp_path: Path):
        limits = AttachmentIngressLimits(max_file_bytes=16)
        paths, rejection = store_inbound_attachments(
            [{"data_url": _data_url("audio/mpeg", b"ID3" + b"\x00" * 64)}],
            media_dir=tmp_path,
            logger=_Logger(),
            limits=limits,
        )
        assert rejection is None
        assert len(paths) == 1

    def test_audio_stays_out_of_the_shared_total(self, tmp_path: Path):
        limits = AttachmentIngressLimits(max_total_bytes=32)
        paths, rejection = store_inbound_attachments(
            [
                {"data_url": _data_url("audio/mpeg", b"ID3" + b"\x00" * 200)},
                {"data_url": _data_url("image/png", b"\x89PNG\r\n\x1a\n")},
            ],
            media_dir=tmp_path,
            logger=_Logger(),
            limits=limits,
        )
        assert rejection is None
        assert len(paths) == 2

    def test_oversized_audio_is_rejected(self, tmp_path: Path, monkeypatch):
        monkeypatch.setattr(attachment_ingress, "_MAX_AUDIO_BYTES", 8)
        paths, rejection = store_inbound_attachments(
            [{"data_url": _data_url("audio/mpeg", b"ID3" + b"\x00" * 64)}],
            media_dir=tmp_path,
            logger=_Logger(),
        )
        assert rejection == "size"
        assert paths == []
        assert list(tmp_path.iterdir()) == []

    def test_browser_recorded_webm_audio_is_not_an_upload_type(self, tmp_path: Path):
        paths, rejection = store_inbound_attachments(
            [{"data_url": _data_url("audio/webm")}],
            media_dir=tmp_path,
            logger=_Logger(),
        )
        assert rejection == "mime"
        assert paths == []
