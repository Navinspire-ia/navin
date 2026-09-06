"""A scanned PDF must reach a vision model as page images, not as an empty file."""

from __future__ import annotations

import asyncio
from pathlib import Path

import pytest

from navin.utils import pdf_pages
from navin.utils.document import extract_documents
from navin.utils.pdf_pages import (
    DEFAULT_MAX_PAGES,
    PdfPages,
    describe_pdf_pages,
    expand_scanned_pdf,
    looks_scanned,
    render_pdf_pages,
)

fitz = pytest.importorskip("fitz", reason="PyMuPDF renders the fixtures")


def _scanned_pdf(path: Path, pages: int = 1) -> Path:
    """A PDF made of drawings only: what a scanner or a phone camera produces."""
    doc = fitz.open()
    for _ in range(pages):
        page = doc.new_page(width=420, height=595)
        page.draw_rect(fitz.Rect(40, 40, 380, 120), color=(0, 0, 0), fill=(0.2, 0.2, 0.2))
        page.draw_line(fitz.Point(40, 200), fitz.Point(380, 200), color=(0, 0, 0), width=3)
    doc.save(str(path))
    doc.close()
    return path


def _text_pdf(path: Path) -> Path:
    doc = fitz.open()
    page = doc.new_page()
    page.insert_text((72, 72), "Invoice 2024-001 total 1200 EUR due in thirty days. " * 4)
    doc.save(str(path))
    doc.close()
    return path


class TestLooksScanned:
    def test_empty_text_over_real_pages_is_scanned(self):
        assert looks_scanned("", 3)
        assert looks_scanned("--- Page 1 ---\n\u00a9", 1)

    def test_prose_is_not_scanned(self):
        assert not looks_scanned("--- Page 1 ---\n" + "word " * 60, 1)

    def test_no_pages_is_never_scanned(self):
        assert not looks_scanned("", 0)


class TestRenderPdfPages:
    def test_missing_file_is_reported_not_raised(self, tmp_path: Path):
        result = render_pdf_pages(tmp_path / "nope.pdf", base_dir=tmp_path / "cache")
        assert not result.ok
        assert result.reason == "file not found"

    def test_renders_png_pages_outside_the_source_directory(self, tmp_path: Path):
        (tmp_path / "docs").mkdir()
        pdf = _scanned_pdf(tmp_path / "docs" / "scan.pdf", pages=3)
        cache = tmp_path / "cache"
        result = render_pdf_pages(pdf, max_pages=2, base_dir=cache)

        assert result.ok
        assert result.total_pages == 3
        assert result.page_numbers == [1, 2]
        assert all(Path(p).suffix == ".png" and Path(p).stat().st_size > 0 for p in result.paths)
        assert all(str(cache) in p for p in result.paths)
        assert list(pdf.parent.iterdir()) == [pdf]

    def test_first_page_lets_a_caller_page_through(self, tmp_path: Path):
        pdf = _scanned_pdf(tmp_path / "scan.pdf", pages=4)
        result = render_pdf_pages(pdf, max_pages=2, first_page=3, base_dir=tmp_path / "cache")
        assert result.page_numbers == [3, 4]

    def test_rendered_pages_are_reused(self, tmp_path: Path):
        pdf = _scanned_pdf(tmp_path / "scan.pdf")
        cache = tmp_path / "cache"
        first = render_pdf_pages(pdf, base_dir=cache)
        stamp = Path(first.paths[0]).stat().st_mtime_ns
        second = render_pdf_pages(pdf, base_dir=cache)
        assert second.paths == first.paths
        assert Path(second.paths[0]).stat().st_mtime_ns == stamp

    def test_no_renderer_is_a_reason_the_model_can_read(self, tmp_path: Path, monkeypatch):
        pdf = _scanned_pdf(tmp_path / "scan.pdf")
        monkeypatch.setattr(pdf_pages, "_RENDERERS", ())
        result = render_pdf_pages(pdf, base_dir=tmp_path / "cache")
        assert not result.ok
        assert "no PDF renderer available" in (result.reason or "")


class TestDescribePdfPages:
    def test_note_states_coverage_so_the_model_cannot_overclaim(self):
        note = describe_pdf_pages(
            PdfPages(
                source="/tmp/contract.pdf",
                total_pages=10,
                paths=["/tmp/p1.png", "/tmp/p2.png"],
                page_numbers=[1, 2],
            )
        )
        assert "contract.pdf" in note
        assert "10 pages" in note
        assert "no text layer" in note
        assert "Pages 1-2 attached as images" in note
        assert "Pages 3-10 are not shown" in note

    def test_ocr_text_follows_the_note(self):
        note = describe_pdf_pages(
            PdfPages(
                source="/tmp/contract.pdf",
                total_pages=3,
                paths=["/tmp/p1.png"],
                page_numbers=[1],
                ocr_text="--- Page 1 (OCR) ---\nARTICLE 1",
                ocr_pages=3,
            )
        )
        assert "OCR text for pages 1-3 follows" in note
        assert "not shown" not in note
        assert note.endswith("ARTICLE 1")

    def test_failure_note_names_the_reason(self):
        note = describe_pdf_pages(PdfPages(source="/tmp/x.pdf", total_pages=2, reason="boom"))
        assert "could not be rendered: boom" in note
        assert "rather than guessing" in note


class TestExpandScannedPdf:
    def test_without_ocr_engine_only_images_travel(self, tmp_path: Path, monkeypatch):
        pdf = _scanned_pdf(tmp_path / "scan.pdf", pages=2)
        monkeypatch.setattr(pdf_pages, "find_tesseract", lambda: None)
        pages = expand_scanned_pdf(pdf, base_dir=tmp_path / "cache")
        assert pages.ok
        assert pages.ocr_text is None
        assert len(pages.paths) == 2

    def test_with_an_ocr_engine_text_covers_more_pages_than_images(self, tmp_path: Path, monkeypatch):
        pdf = _scanned_pdf(tmp_path / "scan.pdf", pages=8)
        monkeypatch.setattr(pdf_pages, "find_tesseract", lambda: "/usr/bin/tesseract")
        monkeypatch.setattr(
            pdf_pages,
            "ocr_page_images",
            lambda paths, numbers, tesseract=None: (
                "\n\n".join(f"--- Page {n} (OCR) ---\ntext {n}" for n in numbers),
                numbers[-1],
            ),
        )
        pages = expand_scanned_pdf(pdf, max_pages=2, base_dir=tmp_path / "cache")
        assert len(pages.paths) == 2
        assert pages.ocr_pages == 8
        assert "text 8" in (pages.ocr_text or "")


class TestExtractDocuments:
    def test_scanned_pdf_becomes_page_images_and_a_note(self, tmp_path: Path, monkeypatch):
        pdf = _scanned_pdf(tmp_path / "scan.pdf", pages=2)
        monkeypatch.setattr(pdf_pages, "pages_cache_root", lambda: tmp_path / "cache")
        monkeypatch.setattr(pdf_pages, "find_tesseract", lambda: None)

        text, images = extract_documents("what does it say?", [str(pdf)])

        assert len(images) == 2
        assert all(Path(p).is_file() for p in images)
        assert "[File: scan.pdf]" in text
        assert "no text layer (scanned document)" in text
        assert "Pages 1-2 attached as images" in text

    def test_text_pdf_still_travels_as_text(self, tmp_path: Path):
        pdf = _text_pdf(tmp_path / "invoice.pdf")
        text, images = extract_documents("check", [str(pdf)])
        assert images == []
        assert "Invoice 2024-001" in text
        assert "no text layer" not in text

    def test_page_budget_is_stated_for_long_scans(self, tmp_path: Path, monkeypatch):
        pdf = _scanned_pdf(tmp_path / "scan.pdf", pages=DEFAULT_MAX_PAGES + 2)
        monkeypatch.setattr(pdf_pages, "pages_cache_root", lambda: tmp_path / "cache")
        monkeypatch.setattr(pdf_pages, "find_tesseract", lambda: None)

        text, images = extract_documents("", [str(pdf)])

        assert len(images) == DEFAULT_MAX_PAGES
        assert f"Pages {DEFAULT_MAX_PAGES + 1}-{DEFAULT_MAX_PAGES + 2} are not shown" in text


class TestReadFileTool:
    def test_read_file_returns_the_pages_of_a_scanned_pdf(self, tmp_path: Path, monkeypatch):
        from navin.agent.tools.file_state import FileStates
        from navin.agent.tools.filesystem import ReadFileTool

        pdf = _scanned_pdf(tmp_path / "scan.pdf", pages=8)
        monkeypatch.setattr(pdf_pages, "pages_cache_root", lambda: tmp_path / "cache")
        monkeypatch.setattr(pdf_pages, "find_tesseract", lambda: None)
        tool = ReadFileTool(workspace=tmp_path, allowed_dir=tmp_path, file_states=FileStates())

        result = asyncio.run(tool.execute(path=str(pdf)))

        assert isinstance(result, list)
        images = [b for b in result if b.get("type") == "image_url"]
        assert len(images) == DEFAULT_MAX_PAGES
        note = result[-1]["text"]
        assert "no text layer" in note
        assert f"Use pages='{DEFAULT_MAX_PAGES + 1}-8' to continue." in note

        tail = asyncio.run(tool.execute(path=str(pdf), pages="7-8"))
        assert len([b for b in tail if b.get("type") == "image_url"]) == 2
        assert "(Page 7 of scan.pdf)" in [b.get("text") for b in tail]
