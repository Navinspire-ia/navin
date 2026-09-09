# Copyright (c) 2026-present Navinspire IA
# SPDX-License-Identifier: AGPL-3.0-only

"""A PDF must reach the WebUI as something it can render, not as `binary`.

`binary` is a dead end in the UI: it draws a download card and nothing else.
PDFs used to land there, so the one document format every module produces
reports in could not be read without leaving the app.
"""

from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from navin.webui.file_preview import file_preview_payload
from navin.webui.workspaces import default_workspace_scope

# Smallest thing a PDF reader will still accept as a document.
_MINIMAL_PDF = (
    b"%PDF-1.4\n"
    b"1 0 obj<</Type/Catalog/Pages 2 0 R>>endobj\n"
    b"2 0 obj<</Type/Pages/Kids[]/Count 0>>endobj\n"
    b"trailer<</Root 1 0 R>>\n"
    b"%%EOF\n"
)


class TestPdfPreviewKind(unittest.TestCase):
    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        self.root = Path(self._tmp.name).resolve()
        self.scope = default_workspace_scope(self.root, True)
        self.addCleanup(self._tmp.cleanup)

    def _write_pdf(self, name: str = "report.pdf") -> Path:
        target = self.root / name
        target.write_bytes(_MINIMAL_PDF)
        return target

    def test_a_pdf_is_announced_as_pdf(self) -> None:
        target = self._write_pdf()
        payload = file_preview_payload(str(target), scope=self.scope, allow_any=True)
        self.assertEqual(payload["kind"], "pdf")

    def test_the_mime_lets_the_browser_pick_its_viewer(self) -> None:
        target = self._write_pdf()
        payload = file_preview_payload(str(target), scope=self.scope, allow_any=True)
        self.assertEqual(payload["mime"], "application/pdf")

    def test_no_body_is_shipped_with_it(self) -> None:
        # The bytes come from the download route instead, like audio and video.
        target = self._write_pdf()
        payload = file_preview_payload(str(target), scope=self.scope, allow_any=True)
        self.assertEqual(payload["content"], "")
        self.assertNotIn("data_url", payload)

    def test_the_size_survives_for_the_inline_cap(self) -> None:
        target = self._write_pdf()
        payload = file_preview_payload(str(target), scope=self.scope, allow_any=True)
        self.assertEqual(payload["size"], len(_MINIMAL_PDF))

    def test_office_formats_render_through_libreoffice_when_present(self) -> None:
        # Word, PowerPoint and Excel files are announced as "office" and say
        # whether the LibreOffice render exists, so the client shows the PDF
        # viewer or the install hint rather than a generic binary card.
        from unittest import mock

        from navin.webui import file_preview

        target = self.root / "book.xlsx"
        target.write_bytes(b"PK\x03\x04\x00\x00binary-ish\x00payload")
        with mock.patch.object(file_preview, "office_render_availability", return_value=(False, "install it")):
            payload = file_preview_payload(str(target), scope=self.scope, allow_any=True)
        self.assertEqual(payload["kind"], "office")
        self.assertFalse(payload["render_available"])
        self.assertEqual(payload["render_hint"], "install it")
        # Outside the editor / chat panel the file stays a plain binary.
        with self.assertRaises(Exception):
            file_preview_payload(str(target), scope=self.scope, allow_any=False)


if __name__ == "__main__":
    unittest.main()
