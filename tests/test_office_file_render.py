"""WebUI inline preview of Word / PowerPoint / Excel files through LibreOffice."""

from __future__ import annotations

import tempfile
import unittest
from pathlib import Path
from unittest import mock

from navin.security.workspace_access import default_workspace_scope
from navin.webui import file_preview
from navin.webui.file_preview import (
    WebUIFilePreviewError,
    file_preview_payload,
    office_render_payload,
)


def _docx(path: Path) -> Path:
    from docx import Document

    document = Document()
    document.add_paragraph("Hello")
    document.save(str(path))
    return path


class OfficePreviewPayloadTests(unittest.TestCase):
    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        self.root = Path(self._tmp.name).resolve()
        self.scope = default_workspace_scope(self.root, True)
        self.addCleanup(self._tmp.cleanup)

    def test_an_office_file_is_announced_with_its_render_availability(self) -> None:
        target = _docx(self.root / "report.docx")
        with mock.patch.object(file_preview, "office_render_availability", return_value=(True, "")):
            payload = file_preview_payload(str(target), scope=self.scope, allow_any=True)
        self.assertEqual(payload["kind"], "office")
        self.assertEqual(payload["language"], "docx")
        self.assertTrue(payload["render_available"])
        self.assertEqual(payload["render_hint"], "")

    def test_without_libreoffice_the_payload_carries_the_install_hint(self) -> None:
        target = _docx(self.root / "report.docx")
        with mock.patch.object(
            file_preview, "office_render_availability", return_value=(False, "Install LibreOffice: apt install libreoffice")
        ):
            payload = file_preview_payload(str(target), scope=self.scope, allow_any=True)
        self.assertEqual(payload["kind"], "office")
        self.assertFalse(payload["render_available"])
        self.assertIn("LibreOffice", payload["render_hint"])

    def test_the_chat_side_view_still_treats_it_as_binary(self) -> None:
        target = _docx(self.root / "report.docx")
        with self.assertRaises(WebUIFilePreviewError) as caught:
            file_preview_payload(str(target), scope=self.scope, allow_any=False)
        self.assertEqual(caught.exception.status, 415)


class OfficeRenderRouteTests(unittest.TestCase):
    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        self.root = Path(self._tmp.name).resolve()
        self.scope = default_workspace_scope(self.root, True)
        self.cache = self.root / "_cache"
        self.addCleanup(self._tmp.cleanup)
        patcher = mock.patch.object(file_preview, "_office_render_cache_dir", return_value=self.cache)
        patcher.start()
        self.addCleanup(patcher.stop)

    def test_other_files_are_refused_with_415(self) -> None:
        other = self.root / "notes.txt"
        other.write_text("x", encoding="utf-8")
        with self.assertRaises(WebUIFilePreviewError) as caught:
            office_render_payload(str(other), scope=self.scope)
        self.assertEqual(caught.exception.status, 415)

    def test_missing_libreoffice_is_a_503_with_the_hint(self) -> None:
        target = _docx(self.root / "report.docx")
        with mock.patch.object(file_preview, "office_render_availability", return_value=(False, "brew install --cask libreoffice")):
            with self.assertRaises(WebUIFilePreviewError) as caught:
                office_render_payload(str(target), scope=self.scope)
        self.assertEqual(caught.exception.status, 503)
        self.assertIn("libreoffice", caught.exception.message)

    def test_a_render_is_served_as_pdf_and_cached_until_the_file_changes(self) -> None:
        import os

        target = _docx(self.root / "report.docx")
        calls: list[Path] = []

        def fake_to_pdf(source, outdir, soffice=None, timeout=0):
            calls.append(Path(source))
            pdf = Path(outdir) / "report.pdf"
            pdf.write_bytes(b"%PDF-1.4 fake " + str(len(calls)).encode())
            return pdf

        with mock.patch.object(file_preview, "office_render_availability", return_value=(True, "")), \
             mock.patch("navin.documents._office.to_pdf", side_effect=fake_to_pdf):
            body, content_type, name = office_render_payload(str(target), scope=self.scope)
            self.assertEqual(content_type, "application/pdf")
            self.assertEqual(name, "report.pdf")
            self.assertTrue(body.startswith(b"%PDF"))
            again, _, _ = office_render_payload(str(target), scope=self.scope)
            self.assertEqual(again, body)
            self.assertEqual(len(calls), 1, "an unchanged file must come from the cache")

            # A new version of the file (size and mtime differ) renders again.
            _docx(target)
            stat = target.stat()
            os.utime(target, ns=(stat.st_atime_ns, stat.st_mtime_ns + 2_000_000_000))
            fresh, _, _ = office_render_payload(str(target), scope=self.scope)
        self.assertEqual(len(calls), 2)
        self.assertNotEqual(fresh, body)

    def test_a_conversion_failure_is_a_500_that_names_libreoffice(self) -> None:
        target = _docx(self.root / "report.docx")
        with mock.patch.object(file_preview, "office_render_availability", return_value=(True, "")), \
             mock.patch("navin.documents._office.to_pdf", side_effect=RuntimeError("soffice exited 1")):
            with self.assertRaises(WebUIFilePreviewError) as caught:
                office_render_payload(str(target), scope=self.scope)
        self.assertEqual(caught.exception.status, 500)
        self.assertIn("LibreOffice could not render", caught.exception.message)


class RouteWiringTests(unittest.TestCase):
    def test_the_http_layer_exposes_file_render_next_to_file_download(self) -> None:
        source = (Path(__file__).resolve().parents[1] / "navin" / "webui" / "ws_http.py").read_text(encoding="utf-8")
        self.assertIn("/file-render", source)
        self.assertIn("office_render_payload", source)
        self.assertIn("to_thread", source, "LibreOffice must not run on the event loop")


if __name__ == "__main__":
    unittest.main()
