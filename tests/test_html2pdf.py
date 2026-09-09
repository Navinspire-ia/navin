# Copyright (c) 2026-present Navinspire IA
# SPDX-License-Identifier: AGPL-3.0-only

"""html2pdf: the print copy, the QA gate, the readback and the real print."""

from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from navin.documents import _pdf, html2pdf
from navin.documents._chromium import find_chromium
from navin.documents.html2pdf import ConversionError

_TWO_PAGES = """<!doctype html><html lang="fr"><head><meta charset="utf-8">
<title>Contrat de prestation</title>
<style>.page{width:210mm;height:297mm;padding:20mm;box-sizing:border-box;font:12pt sans-serif}</style>
</head><body>
<section class="page"><h1>Article 1</h1><p>Le prestataire s'engage a executer les services avec soin.</p></section>
<section class="page"><h1>Article 2</h1><p>Le client regle chaque facture sous trente jours.</p></section>
</body></html>"""


def _text_pdf(path: Path, title: str = "Report") -> None:
    from reportlab.lib.pagesizes import A4
    from reportlab.pdfgen import canvas

    page = canvas.Canvas(str(path), pagesize=A4)
    page.setTitle(title)
    for i in range(10):
        page.drawString(72, 800 - 16 * i, f"Line {i} of a real text layer, selectable and searchable.")
    page.save()


class PrintCopyTests(unittest.TestCase):
    def test_the_copy_resolves_assets_and_counts_the_sheets(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            source = Path(tmp) / "document.html"
            source.write_text(_TWO_PAGES, encoding="utf-8")
            copy, sections = html2pdf.prepare_print_copy(
                source, Path(tmp), paper="a4", landscape=False
            )
            html = copy.read_text(encoding="utf-8")
        self.assertEqual(sections, 2)
        self.assertIn(f'<base href="{source.parent.as_uri()}/">', html)
        self.assertIn("__navin_print__", html)
        self.assertIn("__navin-last", html)
        # No @page in the source: ours provides the A4 sheet.
        self.assertIn("@page { size: 210mm 297mm; margin: 0; }", html)

    def test_the_templates_own_page_rule_wins_for_a4_portrait(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            source = Path(tmp) / "document.html"
            source.write_text(
                _TWO_PAGES.replace("<style>", "<style>@page{size:A4;margin:0}"), encoding="utf-8"
            )
            copy, _ = html2pdf.prepare_print_copy(source, Path(tmp), paper="a4", landscape=False)
            self.assertNotIn("__navin_page__", copy.read_text(encoding="utf-8"))
            copy, _ = html2pdf.prepare_print_copy(source, Path(tmp), paper="letter", landscape=True)
            self.assertIn("size: 11in 8.5in", copy.read_text(encoding="utf-8"))


class QaGateTests(unittest.TestCase):
    def test_a_failed_word_qa_blocks_the_print(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            folder = Path(tmp)
            (folder / "qa.json").write_text(
                json.dumps({"pass": False, "score": 61, "threshold": 80, "rework": ["contrast"]}),
                encoding="utf-8",
            )
            with self.assertRaises(ConversionError) as caught:
                html2pdf.check_qa(folder)
            self.assertIn("word_qa", str(caught.exception))
            self.assertIn("contrast", str(caught.exception))
            # --force prints anyway and only says so on stderr.
            html2pdf.check_qa(folder, force=True)

    def test_a_passing_or_absent_gate_lets_the_print_through(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            folder = Path(tmp)
            html2pdf.check_qa(folder)
            (folder / "critique.json").write_text(json.dumps({"pass": True, "score": 91}), encoding="utf-8")
            html2pdf.check_qa(folder)
            (folder / "qa.json").write_text("not json", encoding="utf-8")
            html2pdf.check_qa(folder)


class ReadbackTests(unittest.TestCase):
    def test_a_titled_text_pdf_has_no_warning(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            pdf = Path(tmp) / "report.pdf"
            _text_pdf(pdf)
            info = html2pdf.inspect_output(pdf, sections=1)
        self.assertEqual(info["pages"], 1)
        self.assertEqual(info["title"], "Report")
        self.assertEqual(info["warnings"], [])

    def test_spill_and_pictures_only_are_named(self) -> None:
        from pypdf import PdfWriter

        with tempfile.TemporaryDirectory() as tmp:
            pdf = Path(tmp) / "blank.pdf"
            writer = PdfWriter()
            writer.add_blank_page(width=595, height=842)
            writer.add_blank_page(width=595, height=842)
            with pdf.open("wb") as handle:
                writer.write(handle)
            info = html2pdf.inspect_output(pdf, sections=1)
        joined = " ".join(info["warnings"])
        self.assertIn("2 pages for 1 .page sections", joined)
        self.assertIn("no page carries selectable text", joined)
        self.assertIn("no title", joined)


class ConvertTests(unittest.TestCase):
    def test_unknown_paper_and_missing_source_are_refused(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            with self.assertRaises(ConversionError):
                html2pdf.convert(str(Path(tmp) / "x.html"), Path(tmp) / "x.pdf", paper="a5")
            with self.assertRaises(ConversionError):
                html2pdf.convert(str(Path(tmp) / "x.html"), Path(tmp) / "x.pdf")

    def test_an_office_file_without_libreoffice_points_back_to_the_html(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            source = Path(tmp) / "report.docx"
            source.write_bytes(b"PK")
            with mock.patch.object(html2pdf, "to_pdf", side_effect=html2pdf.OfficeUnavailableError("LibreOffice missing.")):
                with self.assertRaises(ConversionError) as caught:
                    html2pdf.convert(str(source), Path(tmp) / "report.pdf")
        self.assertIn("html2pdf document.html", str(caught.exception))

    def test_an_office_file_is_converted_and_read_back(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            source = Path(tmp) / "report.docx"
            source.write_bytes(b"PK")
            output = Path(tmp) / "out" / "report.pdf"

            def fake_to_pdf(path, outdir, soffice=None, timeout=0):
                outdir.mkdir(parents=True, exist_ok=True)
                produced = outdir / "report.pdf"
                _text_pdf(produced, title="Report")
                return produced

            with mock.patch.object(html2pdf, "to_pdf", side_effect=fake_to_pdf):
                info = html2pdf.convert(str(source), output)
            self.assertTrue(output.is_file())
        self.assertEqual(info["pages"], 1)
        self.assertEqual(info["warnings"], [])

    @unittest.skipUnless(find_chromium(None), "Chromium is not installed")
    def test_two_sections_print_as_two_a4_pages_with_real_text(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            source = Path(tmp) / "document.html"
            source.write_text(_TWO_PAGES, encoding="utf-8")
            output = Path(tmp) / "document.pdf"
            info = html2pdf.convert(str(source), output, timeout=120)
            self.assertEqual(info["pages"], 2, info)
            self.assertEqual(info["title"], "Contrat de prestation")
            self.assertEqual(info["warnings"], [])
            self.assertEqual(info["paper"], "a4")
            texts = _pdf.page_texts(output)
            self.assertIn("Article 2", texts[1])
            # An A4 sheet is 595 x 842 pt, give or take rounding.
            from pypdf import PdfReader

            box = PdfReader(str(output)).pages[0].mediabox
            self.assertAlmostEqual(float(box.width), 595, delta=2)
            self.assertAlmostEqual(float(box.height), 842, delta=2)

    @unittest.skipUnless(find_chromium(None), "Chromium is not installed")
    def test_a_folder_of_slides_is_bound_into_one_deck_pdf(self) -> None:
        slide = """<!doctype html><html><head><meta charset="utf-8"><title>Kickoff</title>
<style>body{margin:0}.nv-slide{width:1920px;height:1080px;background:#123;color:#fff;font:64px sans-serif;padding:80px;box-sizing:border-box}</style>
</head><body><section class="nv-slide"><h1>Slide %d</h1><p>Real text on the slide.</p></section></body></html>"""
        with tempfile.TemporaryDirectory() as tmp:
            folder = Path(tmp) / "deck"
            folder.mkdir()
            for i in (1, 2, 3):
                (folder / f"slide_{i:02d}.html").write_text(slide % i, encoding="utf-8")
            output = Path(tmp) / "deck.pdf"
            info = html2pdf.convert(str(folder), output, timeout=180)
            self.assertEqual(info["pages"], 3, info)
            self.assertEqual(info["paper"], "slide")
            self.assertEqual(info["title"], "Kickoff")
            self.assertEqual(info["warnings"], [])
            self.assertIn("Slide 3", _pdf.page_texts(output)[2])


class CliTests(unittest.TestCase):
    def test_the_cli_reports_errors_with_a_non_zero_exit(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            code = html2pdf.main([str(Path(tmp) / "missing.html"), "-o", str(Path(tmp) / "x.pdf")])
        self.assertEqual(code, 1)


if __name__ == "__main__":
    unittest.main()
