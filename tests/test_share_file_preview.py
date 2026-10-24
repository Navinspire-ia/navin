# Copyright (c) 2026-present Navinspire IA
# SPDX-License-Identifier: AGPL-3.0-only

"""What a guest's file viewer receives for each kind of project file."""

from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from navin.share.files import SHEETS_MIME, preview_project_file, read_project_file
from navin.webui.file_preview import WebUIFilePreviewError


class ShareFilePreviewTest(unittest.TestCase):
    def setUp(self) -> None:
        self.root = Path(tempfile.mkdtemp())

    def test_markdown_and_media_keep_their_bytes(self) -> None:
        (self.root / "README.md").write_text("# Title\n", encoding="utf-8")
        data, mime, name = preview_project_file(self.root, "README.md")
        self.assertEqual(data, b"# Title\n")
        self.assertEqual(name, "README.md")
        self.assertIn("markdown", mime)

    def test_excel_comes_as_a_grid(self) -> None:
        try:
            import openpyxl
        except ImportError:
            self.skipTest("openpyxl missing")
        book = openpyxl.Workbook()
        book.active.title = "Ventes"
        book.active.append(["Mois", "Total"])
        book.active.append(["Janvier", 12])
        book.save(self.root / "report.xlsx")
        data, mime, name = preview_project_file(self.root, "report.xlsx")
        self.assertEqual(mime, SHEETS_MIME)
        self.assertEqual(name, "report.xlsx")
        sheets = json.loads(data)["sheets"]
        self.assertEqual(sheets[0]["name"], "Ventes")
        self.assertEqual(sheets[0]["rows"][1], ["Janvier", "12"])

    def test_word_renders_to_pdf(self) -> None:
        (self.root / "memo.docx").write_bytes(b"fake")
        with patch("navin.webui.file_preview.render_office_pdf", return_value=b"%PDF-1.7"):
            data, mime, name = preview_project_file(self.root, "memo.docx")
        self.assertEqual((data, mime, name), (b"%PDF-1.7", "application/pdf", "memo.pdf"))

    def test_without_libreoffice_the_guest_downloads(self) -> None:
        (self.root / "deck.pptx").write_bytes(b"fake")
        with patch(
            "navin.webui.file_preview.render_office_pdf",
            side_effect=WebUIFilePreviewError(503, "LibreOffice is required"),
        ):
            with self.assertRaisesRegex(ValueError, "no_preview"):
                preview_project_file(self.root, "deck.pptx")
        # The download itself still works.
        self.assertEqual(read_project_file(self.root, "deck.pptx")[0], b"fake")

    def test_same_guards_as_the_download(self) -> None:
        (self.root / ".env").write_text("KEY=1", encoding="utf-8")
        with self.assertRaisesRegex(ValueError, "not_allowed"):
            preview_project_file(self.root, ".env")
        with self.assertRaisesRegex(ValueError, "not_found"):
            preview_project_file(self.root, "../etc/passwd")


if __name__ == "__main__":
    unittest.main()
